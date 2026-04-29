import win32gui
import win32con
import win32api
import time
import os
import cv2
import numpy as np
import mss
import threading
import tkinter as tk

# ------------------------------------------------------------
# 1. Поиск окна и захват скрина
# ------------------------------------------------------------
def get_game_window():
    """Ищет окно Mindustry по заголовку."""
    def enum_callback(hwnd, windows):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Mindustry" in title:
                windows.append(hwnd)
    windows = []
    win32gui.EnumWindows(enum_callback, windows)
    if not windows:
        raise Exception("Окно Mindustry не найдено")
    return windows[0]

def capture_window(hwnd):
    """Захватывает клиентскую область окна (без рамок)."""
    rect = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    region = {
        'left': pt[0],
        'top': pt[1],
        'width': rect[2],
        'height': rect[3]
    }
    with mss.MSS() as sct:
        img = sct.grab(region)
        # BGRA -> BGR
        return np.array(img)[..., :3]

# ------------------------------------------------------------
# 2. Работа с шаблонами руд
# ------------------------------------------------------------
def load_ore_templates(data_dir="data/ores"):
    templates = {}  # {"copper": [tpl1, tpl2, tpl3], ...}
    if not os.path.isdir(data_dir):
        print(f"Папка {data_dir} не найдена")
        return templates

    for ore_name in os.listdir(data_dir):
        ore_path = os.path.join(data_dir, ore_name)
        if not os.path.isdir(ore_path):
            continue  # пропускаем файлы, если вдруг есть не в папках

        ore_templates = []
        for filename in os.listdir(ore_path):
            if filename.lower().endswith(".png"):
                path = os.path.join(ore_path, filename)
                tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if tpl is not None:
                    ore_templates.append(tpl)
                else:
                    print(f"Ошибка загрузки {path}")
        if ore_templates:
            templates[ore_name.lower()] = ore_templates
        else:
            print(f"В папке {ore_name} нет загруженных шаблонов")
    return templates

def find_resources(screen_bgr, templates):
    found = []
    gray_screen = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

    for ore_name, tpl_list in templates.items():
        for tpl in tpl_list:
            h, w = tpl.shape[:2]
            res = cv2.matchTemplate(gray_screen, tpl, cv2.TM_CCOEFF_NORMED)
            # Немного снизим порог, чтобы ловить вариации
            threshold = 0.75
            loc = np.where(res >= threshold)
            for pt in zip(*loc[::-1]):
                x = pt[0] + w // 2
                y = pt[1] + h // 2
                found.append((ore_name, x, y))
    return found

# ------------------------------------------------------------
# 3. Управление: клики и перетаскивание
# ------------------------------------------------------------
def click_window(hwnd, x, y, double=True):
    lparam = win32api.MAKELONG(x, y)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.05)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    if double:
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)

def drag_window(hwnd, x1, y1, x2, y2, steps=10):
    """Перетаскивание от (x1,y1) к (x2,y2) внутри окна."""
    lparam = win32api.MAKELONG(x1, y1)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.02)
    dx = (x2 - x1) / steps
    dy = (y2 - y1) / steps
    for i in range(1, steps + 1):
        cur_x = int(x1 + dx * i)
        cur_y = int(y1 + dy * i)
        lparam = win32api.MAKELONG(cur_x, cur_y)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, lparam)
        time.sleep(0.01)
    lparam = win32api.MAKELONG(x2, y2)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)

def show_click_marker(screen_x, screen_y, duration=300):
    def _mark():
        root = tk.Tk()
        root.overrideredirect(True)
        root.wm_attributes('-topmost', True)
        root.wm_attributes('-transparentcolor', 'white')
        canvas = tk.Canvas(root, width=2, height=2, bg='white', highlightthickness=0)
        canvas.create_oval(0.1, 0.1, 2, 2, fill='red', outline='red')
        root.geometry(f'+{screen_x-8}+{screen_y-8}')
        root.after(duration, root.destroy)
        root.mainloop()
    threading.Thread(target=_mark, daemon=True).start()
# ------------------------------------------------------------
# 4. Основной цикл
# ------------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(script_dir, "data", "ores")
    
    print("Текущая папка:", os.getcwd())
    print("Папка со скриптом:", script_dir)
    print("Путь к шаблонам:", data_path)
    
    templates = load_ore_templates(data_path)
    if not templates:
        print("Не загружено ни одного шаблона. Проверь папку data/ores/")
        return

    game_hwnd = get_game_window()
    print("Бот запущен. Переключись в свою игру на основном мониторе.")
    
    # Получаем размеры окна один раз, чтобы знать центр
    rect = win32gui.GetClientRect(game_hwnd)
    center_x, center_y = rect[2] // 2, rect[3] // 2

    while True:
        screen = capture_window(game_hwnd)
        resources = find_resources(screen, templates)
        if resources:
            # Сортируем по расстоянию до центра окна (заглушка, позже – до ядра)
            resources.sort(key=lambda r: (r[1]-center_x)**2 + (r[2]-center_y)**2)
            name, x, y = resources[0]
            print(f"Копаем {name} в ({x},{y})")
            
            screen_pt = win32gui.ClientToScreen(game_hwnd, (x, y))
            show_click_marker(screen_pt[0], screen_pt[1])
            
            click_window(game_hwnd, x, y)
            time.sleep(0.5)
        time.sleep(0.3)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ОШИБКА:", e)
        import traceback
        traceback.print_exc()
    input("Нажми Enter для выхода...")