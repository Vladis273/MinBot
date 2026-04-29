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
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ
# ------------------------------------------------------------
class BotConfig:
    """Конфигурация бота."""
    # Ресурсы для поиска. Порядок важен: приоритет сверху вниз.
    # Медь/Свинец - основные ресурсы. Уголь добавляется динамически для Гаммы.
    ORES = ["copper", "lead"]
    COAL_ENABLED = False  # Включить добычу угля (будет активировано автоматически при обнаружении юнита Гамма)
    # Пороги поиска (можно понизить, если руда плохо находится)
    MATCH_THRESHOLD = 0.70
    # Тайминги
    CLICK_DELAY = 0.05
    MINING_CHECK_DELAY = 0.3  # Задержка перед проверкой начала добычи
    MOVE_STEP_DELAY = 0.02
    MINING_DURATION = 2.0     # Примерное время добычи одной жилы
    # Движение
    MOVE_KEYS = {
        'up': 0x57,      # W
        'down': 0x53,    # S
        'left': 0x41,    # A
        'right': 0x44    # D
    }
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
# 2. Работа с шаблонами руд и юнитов
# ------------------------------------------------------------
def load_ore_templates(data_dir="data/sprites/blocks/environment"):
    """Загружает шаблоны ресурсов из указанной папки."""
    templates = {}
    if not os.path.isdir(data_dir):
        print(f"Папка {data_dir} не найдена")
        return templates
    # Добавляем песок в список, если его нет
    resources_to_load = BotConfig.ORES.copy()
    for ore_name in resources_to_load:
        ore_templates = []
        for i in range(1, 4):  # 1, 2, 3
            filename = f"ore-{ore_name}{i}.png"
            path = os.path.join(data_dir, filename)
            if os.path.isfile(path):
                tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if tpl is not None:
                    ore_templates.append(tpl)
                else:
                    print(f"Ошибка загрузки {path}")
            # Если файл не найден - просто пропускаем (не все ресурсы имеют 3 текстуры)
        if ore_templates:
            templates[ore_name] = ore_templates
            print(f"Загружено {len(ore_templates)} шаблонов для {ore_name}")
        else:
            print(f"Не найдено шаблонов для {ore_name}")
    return templates
def load_unit_templates(data_dir="data/sprites/units"):
    """Загружает шаблоны юнитов для определения типа (Альфа, Бета, Гамма)."""
    unit_templates = {}
    unit_names = ["alpha", "beta", "gamma"]
    if not os.path.isdir(data_dir):
        print(f"Папка юнитов {data_dir} не найдена")
        return unit_templates
    for unit_name in unit_names:
        # Ищем файлы вида alpha.png, alpha-0.png и т.д.
        found_templates = []
        for filename in os.listdir(data_dir):
            if filename.lower().startswith(unit_name) and filename.endswith(".png"):
                path = os.path.join(data_dir, filename)
                tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if tpl is not None:
                    found_templates.append(tpl)
        if found_templates:
            unit_templates[unit_name] = found_templates
            print(f"Загружено {len(found_templates)} шаблонов для юнита {unit_name}")
    return unit_templates
def detect_current_unit(screen_bgr, unit_templates):
    """Определяет текущего юнита по экрану. Возвращает 'alpha', 'beta', 'gamma' или None."""
    if not unit_templates:
        return None
    gray_screen = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    # Ищем юнита с наивысшим совпадением
    best_match = None
    best_score = 0.8  # Порог обнаружения юнита
    for unit_name, tpl_list in unit_templates.items():
        for tpl in tpl_list:
            res = cv2.matchTemplate(gray_screen, tpl, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = max_val
                best_match = unit_name
    if best_match:
        print(f"Обнаружен юнит: {best_match} (уверенность: {best_score:.2f})")
        return best_match
    return None
def find_resources(screen_bgr, templates, excluded_positions=None):
    """
    Ищет ресурсы на экране.
    excluded_positions: список координат (x, y), которые нужно игнорировать (уже добываемые).
    """
    found = []
    gray_screen = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    for ore_name, tpl_list in templates.items():
        for tpl in tpl_list:
            h, w = tpl.shape[:2]
            res = cv2.matchTemplate(gray_screen, tpl, cv2.TM_CCOEFF_NORMED)
            threshold = BotConfig.MATCH_THRESHOLD
            loc = np.where(res >= threshold)
            # Применяем non-maximum suppression (упрощенно) - не добавляем дубликаты рядом
            for pt in zip(*loc[::-1]):
                x = pt[0] + w // 2
                y = pt[1] + h // 2
                # Проверяем, не исключена ли эта позиция
                if excluded_positions:
                    is_excluded = False
                    for ex_x, ex_y in excluded_positions:
                        if abs(x - ex_x) < 20 and abs(y - ex_y) < 20:
                            is_excluded = True
                            break
                    if is_excluded:
                        continue
                found.append((ore_name, x, y))
    # Удаляем близкие дубликаты (одна и та же руда найдена по разным шаблонам)
    unique_found = []
    for item in found:
        is_duplicate = False
        for existing in unique_found:
            if abs(item[1] - existing[1]) < 15 and abs(item[2] - existing[2]) < 15:
                is_duplicate = True
                break
        if not is_duplicate:
            unique_found.append(item)
    return unique_found
# ------------------------------------------------------------
# 3. Управление: клики, движение и перетаскивание
# ------------------------------------------------------------
def click_window(hwnd, x, y, double=True):
    """Отправляет клик в окно по координатам внутри окна."""
    lparam = win32api.MAKELONG(x, y)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(BotConfig.CLICK_DELAY)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    if double:
        time.sleep(BotConfig.CLICK_DELAY)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(BotConfig.CLICK_DELAY)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
def send_key(hwnd, vk_code, is_pressed):
    """Отправляет нажатие/отпускание клавиши в окно."""
    if is_pressed:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk_code, 0)
    else:
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk_code, 0)
def move_to_target(hwnd, target_x, target_y, window_rect, max_iterations=15):
    """
    Двигает юнита к цели с помощью WASD.
    target_x, target_y - координаты цели внутри окна.
    Возвращает True если успешно приблизился.
    """
    center_x = window_rect[2] // 2
    center_y = window_rect[3] // 2
    for _ in range(max_iterations):
        # Определяем направление движения
        dx = target_x - center_x
        dy = target_y - center_y
        # Нормализуем и определяем основные направления
        threshold = 40  # Мертвая зона в центре
        if abs(dx) < threshold and abs(dy) < threshold:
            return True  # Уже в центре (достаточно близко)
        # Отправляем команды движения (можно комбинировать для диагоналей)
        moved = False
        # Вертикальное движение
        if dy < -threshold:  # Цель выше центра -> летим вверх (W)
            send_key(hwnd, BotConfig.MOVE_KEYS['up'], True)
            time.sleep(BotConfig.MOVE_STEP_DELAY)
            send_key(hwnd, BotConfig.MOVE_KEYS['up'], False)
            moved = True
        elif dy > threshold:  # Цель ниже -> вниз (S)
            send_key(hwnd, BotConfig.MOVE_KEYS['down'], True)
            time.sleep(BotConfig.MOVE_STEP_DELAY)
            send_key(hwnd, BotConfig.MOVE_KEYS['down'], False)
            moved = True
        # Горизонтальное движение
        if dx < -threshold:  # Цель левее -> влево (A)
            send_key(hwnd, BotConfig.MOVE_KEYS['left'], True)
            time.sleep(BotConfig.MOVE_STEP_DELAY)
            send_key(hwnd, BotConfig.MOVE_KEYS['left'], False)
            moved = True
        elif dx > threshold:  # Цель правее -> вправо (D)
            send_key(hwnd, BotConfig.MOVE_KEYS['right'], True)
            time.sleep(BotConfig.MOVE_STEP_DELAY)
            send_key(hwnd, BotConfig.MOVE_KEYS['right'], False)
            moved = True
        if moved:
            time.sleep(0.05)  # Небольшая задержка после движения
        else:
            break
    # Финальная проверка
    dx = target_x - center_x
    dy = target_y - center_y
    return abs(dx) < 60 and abs(dy) < 60
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
def check_mining_started(hwnd, screen_before, ore_x, ore_y, templates):
    """
    Проверяет, началась ли добыча руды.
    После начала добычи появляется анимация, которая меняет текстуру.
    Если текстура изменилась - значит копание началось.
    Возвращает True если добыча началась.
    """
    time.sleep(BotConfig.MINING_CHECK_DELAY)
    screen_after = capture_window(hwnd)
    # Берем область вокруг точки клика
    check_radius = 20
    gray_before = cv2.cvtColor(screen_before, cv2.COLOR_BGR2GRAY)
    gray_after = cv2.cvtColor(screen_after, cv2.COLOR_BGR2GRAY)
    h, w = gray_before.shape
    x1, y1 = max(0, ore_x - check_radius), max(0, ore_y - check_radius)
    x2, y2 = min(w, ore_x + check_radius), min(h, ore_y + check_radius)
    region_before = gray_before[y1:y2, x1:x2]
    region_after = gray_after[y1:y2, x1:x2]
    # Сравниваем гистограммы областей
    hist_before = cv2.calcHist([region_before], [0], None, [256], [0, 256])
    hist_after = cv2.calcHist([region_after], [0], None, [256], [0, 256])
    correlation = cv2.compareHist(hist_before, hist_after, cv2.HISTCMP_CORREL)
    # Если корреляция низкая (< 0.9), значит текстура изменилась (анимация добычи)
    return correlation < 0.9
# ------------------------------------------------------------
# 4. Основной цикл
# ------------------------------------------------------------
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ore_data_path = os.path.join(script_dir, "data", "sprites", "blocks", "environment")
    unit_data_path = os.path.join(script_dir, "data", "sprites", "units")
    print("Текущая папка:", os.getcwd())
    print("Папка со скриптом:", script_dir)
    print("Путь к шаблонам руд:", ore_data_path)
    print("Путь к шаблонам юнитов:", unit_data_path)
    # Загружаем шаблоны руд
    templates = load_ore_templates(ore_data_path)
    if not templates:
        print("Не загружено ни одного шаблона руды. Проверь папку data/sprites/blocks/environment/")
        return
    # Загружаем шаблоны юнитов
    unit_templates = load_unit_templates(unit_data_path)
    game_hwnd = get_game_window()
    print("Бот запущен. Переключись в свою игру на основном мониторе.")
    # Получаем размеры окна
    rect = win32gui.GetClientRect(game_hwnd)
    center_x, center_y = rect[2] // 2, rect[3] // 2
    # Переменные состояния
    current_unit = None
    mining_target = None  # (x, y) текущей добываемой жилы
    mining_start_time = 0
    is_mining = False
    # Определяем юнита при старте
    if unit_templates:
        initial_screen = capture_window(game_hwnd)
        current_unit = detect_current_unit(initial_screen, unit_templates)
        if current_unit == "gamma":
            BotConfig.COAL_ENABLED = True
            # Динамически добавляем уголь в список ресурсов
            if "coal" not in templates:
                coal_path = os.path.join(ore_data_path, "ore-coal")
                coal_tpls = []
                for i in range(1, 4):
                    cpath = f"{coal_path}{i}.png"
                    if os.path.isfile(cpath):
                        tpl = cv2.imread(cpath, cv2.IMREAD_GRAYSCALE)
                        if tpl is not None:
                            coal_tpls.append(tpl)
                if coal_tpls:
                    templates["coal"] = coal_tpls
                    print("Добыча угля включена (шаблоны загружены)")
            print(f"Юнит Гамма обнаружен. Добыча угля: {'включена' if BotConfig.COAL_ENABLED else 'выключена'}")
        elif current_unit:
            print(f"Юнит {current_unit.capitalize()} обнаружен. Уголь не доступен.")
    while True:
        try:
            screen = capture_window(game_hwnd)
            # Периодически переопределяем юнита (каждые 10 циклов)
            if unit_templates and random.randint(0, 10) == 0:
                detected = detect_current_unit(screen, unit_templates)
                if detected and detected != current_unit:
                    current_unit = detected
                    if current_unit == "gamma":
                        BotConfig.COAL_ENABLED = True
            # Исключаем текущую цель из поиска, чтобы не переключаться
            excluded = [mining_target] if mining_target and is_mining else None
            resources = find_resources(screen, templates, excluded_positions=excluded)
            if is_mining and mining_target:
                # Проверяем, продолжается ли добыча
                tx, ty = mining_target
                # Проверяем по времени - если прошло больше времени чем длительность добычи
                if time.time() - mining_start_time > BotConfig.MINING_DURATION:
                    print("Добыча завершена (по таймеру)")
                    is_mining = False
                    mining_target = None
                    continue
                # Проверяем визуально - началась ли анимация
                if not check_mining_started(game_hwnd, screen, tx, ty, templates):
                    # Анимация пропала или не началась - возможно руда кончилась
                    # Делаем еще один клик для уверенности
                    print("Повторная попытка начать добычу...")
                    click_window(game_hwnd, tx, ty)
                    mining_start_time = time.time()
                continue
            # Если не добываем, ищем новую цель
            if resources:
                # Сортируем по расстоянию до центра окна (позже можно сделать до ядра)
                resources.sort(key=lambda r: (r[1]-center_x)**2 + (r[2]-center_y)**2)
                name, x, y = resources[0]
                print(f"Найдена {name} в ({x},{y}). Приближаемся...")
                # Двигаемся к цели
                if move_to_target(game_hwnd, x, y, rect):
                    print(f"Позиция занята. Начинаем добычу {name}...")
                    # Показываем маркер клика
                    screen_pt = win32gui.ClientToScreen(game_hwnd, (x, y))
                    show_click_marker(screen_pt[0], screen_pt[1])
                    # Двойной клик для начала добычи
                    click_window(game_hwnd, x, y)
                    mining_target = (x, y)
                    mining_start_time = time.time()
                    is_mining = True
                    # Ждем подтверждения начала анимации
                    time.sleep(0.5)
                else:
                    print("Не удалось приблизиться к ресурсу")
            time.sleep(0.2)
        except Exception as e:
            print(f"Ошибка в цикле: {e}")
            time.sleep(1)
if __name__ == "__main__":
    import random  # Добавляем для случайных проверок
    try:
        main()
    except Exception as e:
        print("ОШИБКА:", e)
        import traceback
        traceback.print_exc()
    input("Нажми Enter для выхода...")
