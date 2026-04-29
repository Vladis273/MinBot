import cv2
import numpy as np
import pyautogui
import time
import os
import sys
import win32gui
import win32con
import win32api
from PIL import ImageGrab
import math

# --- КОНФИГУРАЦИЯ ---
CONFIG = {
    'game_title': 'Mindustry',  # Заголовок окна игры (может потребоваться уточнение)
    'confidence_threshold': 0.8, # Порог уверенности для поиска
    'unit_check_interval': 2.0,  # Проверка юнита раз в 2 секунды
    'mining_duration': 2.5,      # Длительность добычи (сек)
    'move_step': 40,             # Шаг движения (пиксели)
    'move_delay': 0.05,          # Задержка между шагами
    'debug': True                # Режим отладки (логи)
}

# Пути (относительно скрипта или абсолютные)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPRITES_DIR = os.path.join(SCRIPT_DIR, 'data', 'sprites')
ORES_DIR = os.path.join(SPRITES_DIR, 'blocks', 'environment')
UNITS_DIR = os.path.join(SPRITES_DIR, 'units')
WEAPON_TEMPLATE_PATH = os.path.join(UNITS_DIR, 'weapons', 'build-weapon.png')

# Ресурсы для поиска
RESOURCES = ['copper', 'lead'] # Уголь добавится автоматически если найдем Гамму

class MinBot:
    def __init__(self):
        self.templates = {}
        self.unit_templates = {}
        self.weapon_template = None
        self.current_unit = None
        self.last_unit_check = 0
        self.current_target = None # Текущая цель (координаты), чтобы не переключаться
        self.is_mining = False
        self.mining_start_time = 0
        
        self.load_templates()
        
    def load_templates(self):
        """Загрузка всех шаблонов"""
        print(f"Путь к рудам: {ORES_DIR}")
        print(f"Путь к юнитам: {UNITS_DIR}")
        print(f"Путь к оружию: {WEAPON_TEMPLATE_PATH}")

        # 1. Загрузка руд
        for res in RESOURCES:
            self.templates[res] = []
            for i in range(1, 4): # ore-copper1.png ... ore-copper3.png
                path = os.path.join(ORES_DIR, f"ore-{res}{i}.png")
                if os.path.exists(path):
                    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        self.templates[res].append(img)
                        if CONFIG['debug']: print(f"Загружено: {path}")
                    else:
                        print(f"Ошибка загрузки: {path}")
                else:
                    # Пробуем формат ore-copper-1.png (иногда бывает через дефис)
                    path_alt = os.path.join(ORES_DIR, f"ore-{res}-{i}.png")
                    if os.path.exists(path_alt):
                        img = cv2.imread(path_alt, cv2.IMREAD_GRAYSCALE)
                        if img is not None:
                            self.templates[res].append(img)
                            if CONFIG['debug']: print(f"Загружено (alt): {path_alt}")

        # 2. Загрузка шаблонов юнитов (для идентификации после нахождения оружия)
        unit_names = ['alpha', 'beta', 'gamma']
        for name in unit_names:
            # Ищем основные файлы юнитов
            paths = [
                os.path.join(UNITS_DIR, f"{name}.png"),
                os.path.join(UNITS_DIR, f"{name}-full.png"),
                os.path.join(UNITS_DIR, name, f"{name}.png") # Если в подпапке
            ]
            for p in paths:
                if os.path.exists(p):
                    img = cv2.imread(p, cv2.IMREAD_COLOR) # Цветной для точности
                    if img is not None:
                        self.unit_templates[name] = img
                        if CONFIG['debug']: print(f"Загружен юнит: {p}")
                        break
            
        # 3. Загрузка оружия (ключевой элемент поиска)
        if os.path.exists(WEAPON_TEMPLATE_PATH):
            self.weapon_template = cv2.imread(WEAPON_TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)
            if CONFIG['debug']: print(f"Загружено оружие: {WEAPON_TEMPLATE_PATH}")
        else:
            print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: Файл оружия не найден: {WEAPON_TEMPLATE_PATH}")
            print("Проверьте наличие файла build-weapon.png в папке data/sprites/units/weapons/")

    def get_game_window(self):
        """Поиск окна игры"""
        # Пытаемся найти по заголовку, если не получится - берем активное
        hwnd = win32gui.FindWindow(None, CONFIG['game_title'])
        if hwnd == 0:
            hwnd = win32gui.GetForegroundWindow()
        return hwnd

    def get_screen_region(self, hwnd=None):
        """Получение скриншота области игры"""
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            # Убираем рамки окна (иногда нужно подкорректировать)
            left += 8
            top += 30
            right -= 8
            bottom -= 8
            
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                return None, (0,0,0,0)
                
            screenshot = ImageGrab.grab(bbox=(left, top, right, bottom))
            return np.array(screenshot), (left, top, right, bottom)
        else:
            screenshot = ImageGrab.grab()
            return np.array(screenshot), (0, 0, screenshot.size[0], screenshot.size[1])

    def find_best_match(self, screen_gray, templates_list, threshold=0.8):
        """Ищет лучшее совпадение среди списка шаблонов"""
        best_val = 0
        best_loc = None
        best_template = None
        
        for tmpl in templates_list:
            if tmpl is None: continue
            w, h = tmpl.shape[::-1]
            if screen_gray.shape[0] < h or screen_gray.shape[1] < w:
                continue
                
            res = cv2.matchTemplate(screen_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            
            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_template = tmpl
                
        if best_val >= threshold:
            return best_loc, best_val, best_template.shape[::-1] # loc, confidence, (w,h)
        return None, 0, None

    def find_weapon_with_rotation(self, screen_gray, threshold=0.75):
        """Ищет оружие с учетом поворотов на 90 градусов"""
        if self.weapon_template is None:
            return None, 0
            
        h, w = self.weapon_template.shape
        rotations = [0, 90, 180, 270]
        
        best_overall_loc = None
        best_overall_val = 0
        best_angle = 0
        
        for angle in rotations:
            if angle == 0:
                rotated = self.weapon_template
            else:
                # Поворот изображения
                k = angle // 90
                rotated = cv2.rotate(self.weapon_template, k)
            
            # matchTemplate требует чтобы шаблон был меньше экрана
            if screen_gray.shape[0] < rotated.shape[0] or screen_gray.shape[1] < rotated.shape[1]:
                continue

            res = cv2.matchTemplate(screen_gray, rotated, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            
            if max_val > best_overall_val:
                best_overall_val = max_val
                best_overall_loc = max_loc
                best_angle = angle

        if best_overall_val >= threshold:
            return best_overall_loc, best_overall_val, best_angle
        return None, 0, 0

    def identify_unit_type(self, screen_color, weapon_loc, weapon_size):
        """Определяет тип юнита по области вокруг оружия"""
        if not self.unit_templates:
            return None, 0.0
            
        wx, wy = weapon_loc
        ww, wh = weapon_size
        
        # Берем область вокруг оружия (примерный размер юнита)
        # Юнит обычно больше оружия. Возьмем зону 100x100 вокруг центра оружия
        margin = 60 
        x1 = max(0, wx - margin)
        y1 = max(0, wy - margin)
        x2 = min(screen_color.shape[1], wx + ww + margin)
        y2 = min(screen_color.shape[0], wy + wh + margin)
        
        roi = screen_color[y1:y2, x1:x2]
        
        if roi.size == 0:
            return None, 0.0
            
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        best_unit = None
        best_conf = 0
        
        for name, tmpl in self.unit_templates.items():
            # Масштабируем шаблон юнита под размер ROI, если они сильно различаются?
            # Лучше использовать matchTemplate, но шаблон юнита должен быть примерно того же размера
            # Или искать шаблон юнита внутри ROI
            
            th, tw, _ = tmpl.shape
            rh, rw, _ = roi.shape
            
            # Если шаблон больше ROI, пропускаем или ресайзим (просто ресайзим для теста)
            if th > rh or tw > rw:
                scaled_tmpl = cv2.resize(tmpl, (rw, rh))
                res = cv2.matchTemplate(roi, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            else:
                res = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
            
            _, max_val, _, _ = cv2.minMaxLoc(res)
            
            if max_val > best_conf:
                best_conf = max_val
                best_unit = name
                
        # Порог для определения юнита довольно низкий, т.к. ROI маленький
        if best_conf > 0.6: 
            return best_unit, best_conf
        return None, best_conf

    def detect_unit(self, screen_color, screen_gray):
        """Основная логика обнаружения юнита"""
        # 1. Ищем оружие
        w_loc, w_conf, w_angle = self.find_weapon_with_rotation(screen_gray)
        
        if w_loc:
            wx, wy = w_loc
            # Получаем размеры оригинального шаблона (до поворота) для оценки зоны
            # Для простоты возьмем размеры повернутого, они те же по модулю (квадратные или нет)
            # Но нам нужно знать размеры исходного для логики, хотя для matchTemplate не критично
            h, w = self.weapon_template.shape
            
            # 2. Определяем тип юнита вокруг оружия
            u_type, u_conf = self.identify_unit_type(screen_color, w_loc, (w, h))
            
            if u_type:
                return u_type, u_conf
            else:
                # Если оружие нашли, а тип нет - возможно это стандартный вид, вернем неизвестный или попробуем угадать по контексту?
                # Пока вернем None, но факт наличия оружия важен
                pass
        
        return None, 0.0

    def move_to(self, target_x, target_y, offset_x=0, offset_y=0):
        """Движение WASD к точке. target - центр экрана относительно которого надо оказаться"""
        # Логика: мы хотим, чтобы target_x, target_y оказались в центре экрана
        # Получаем размеры экрана
        screen_w = pyautogui.size().width
        screen_h = pyautogui.size().height
        center_x, center_y = screen_w // 2, screen_h // 2
        
        # Разница
        dx = (target_x + offset_x) - center_x
        dy = (target_y + offset_y) - center_y
        
        # Мертвая зона
        if abs(dx) < 20 and abs(dy) < 20:
            return True # Достигли
            
        steps = 0
        max_steps = 50 # Защита от бесконечности
        
        # Нормализация направления
        while (abs(dx) > 20 or abs(dy) > 20) and steps < max_steps:
            if dx > 20:
                pyautogui.keyDown('d')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('d')
            elif dx < -20:
                pyautogui.keyDown('a')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('a')
                
            if dy > 20:
                pyautogui.keyDown('s')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('s')
            elif dy < -20:
                pyautogui.keyDown('w')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('w')
            
            # Пересчитываем (в реальности нужно читать позицию, но тут эмуляция)
            # В данной реализации мы просто делаем рывки. 
            # Для идеальной работы нужно считывать координаты игрока из памяти или постоянно сканировать центр.
            # Здесь упрощенно: делаем шаг и ждем.
            time.sleep(0.1)
            steps += 1
            
            # Обновляем "предполагаемую" дельту (очень грубо)
            # В реальной игре без чтения памяти мы не знаем, сдвинулись ли мы.
            # Поэтому этот метод работает только если игра реагирует мгновенно и мы видим изменения на скриншоте.
            # ДЛЯ НАДЕЖНОСТИ: просто нажимаем клавиши в сторону цели фиксированное время?
            # Нет, лучше сделать цикл с проверкой скриншота.
            break # Выходим, чтобы не зависнуть без обратной связи. Реализация ниже будет сложнее.
            
        # УПРОЩЕННАЯ РЕАЛИЗАЦИЯ ДВИЖЕНИЯ (нажимает и держит пока не приблизится визуально)
        # Это требует цикла с захватом экрана внутри, что медленно.
        # Оставим пока как "рывки".
        return False

    def smart_move_to(self, target_x, target_y, region_offset=(0,0)):
        """Умное движение с проверкой скриншота"""
        hwnd = self.get_game_window()
        if not hwnd: return
        
        screen_w = pyautogui.size().width
        screen_h = pyautogui.size().height
        center_x, center_y = screen_w // 2, screen_h // 2
        
        # Целевая точка на экране (абсолютная)
        # target_x/y относительны окна игры? Да, в find_best_match возвращаются относительные.
        # Нам нужно добавить смещение окна, чтобы получить абсолютные координаты для понимания "где я"
        # Но pyautogui двигает курсор/камеру относительно экрана.
        # В Mindustry камера двигается. Мы хотим, чтобы объект оказался в центре.
        
        # Допустим, объект сейчас в (target_x + offset_left, target_y + offset_top) на полном экране.
        # Мы хотим сдвинуть камеру так, чтобы он стал в (center_x, center_y).
        
        # Простая эвристика: нажимаем кнопки, пока объект не окажется близко к центру.
        max_iterations = 40
        tolerance = 30
        
        for _ in range(max_iterations):
            screen_np, offset = self.get_screen_region(hwnd)
            if screen_np is None: break
            
            # Находим объект снова на свежем скриншоте, чтобы понять где он сейчас
            # Это дорого, но надежно. Ищем только шаблон ресурса? Нет, мы не знаем какой именно.
            # Просто проверяем смещение целевой точки относительно центра.
            # Но мы не знаем, сдвинулась ли камера.
            # ПРИ ДОПУЩЕНИИ: Камера сдвигается ровно на величину нажатия.
            
            # Вычисляем текущее смещение объекта от центра (изначально)
            # Абсолютные координаты объекта в мире игры нам неизвестны.
            # Мы знаем его пиксели на экране: obj_screen_x = target_x + offset[0]
            obj_screen_x = target_x + offset[0]
            obj_screen_y = target_y + offset[1]
            
            dx = obj_screen_x - center_x
            dy = obj_screen_y - center_y
            
            if abs(dx) < tolerance and abs(dy) < tolerance:
                return True # В центре
            
            # Двигаем камеру в противоположную сторону от смещения
            # Если объект справа (dx > 0), надо двигать камеру вправо (клавиша D)
            if dx > tolerance:
                pyautogui.keyDown('d')
                time.sleep(0.15)
                pyautogui.keyUp('d')
            elif dx < -tolerance:
                pyautogui.keyDown('a')
                time.sleep(0.15)
                pyautogui.keyUp('a')
                
            if dy > tolerance:
                pyautogui.keyDown('s')
                time.sleep(0.15)
                pyautogui.keyUp('s')
            elif dy < -tolerance:
                pyautogui.keyDown('w')
                time.sleep(0.15)
                pyautogui.keyUp('w')
                
            time.sleep(0.05)
            
        return False

    def click(self, x, y, button='left'):
        """Клик в координатах относительно окна игры"""
        hwnd = self.get_game_window()
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            abs_x = rect[0] + 8 + x # Учет рамок
            abs_y = rect[1] + 30 + y
            pyautogui.click(abs_x, abs_y, button=button)
        else:
            pyautogui.click(x, y, button=button)

    def run(self):
        print("Бот запущен. Переключись в свою игру на основном мониторе.")
        time.sleep(2)
        
        hwnd = self.get_game_window()
        if not hwnd:
            print("Не удалось найти окно игры!")
            return

        while True:
            current_time = time.time()
            
            # 1. Определение юнита (раз в N секунд)
            if current_time - self.last_unit_check > CONFIG['unit_check_interval']:
                screen_color, _ = self.get_screen_region(hwnd)
                if screen_color is not None:
                    screen_gray = cv2.cvtColor(screen_color, cv2.COLOR_BGR2GRAY)
                    u_type, conf = self.detect_unit(screen_color, screen_gray)
                    
                    if u_type:
                        if self.current_unit != u_type:
                            print(f"Обнаружен юнит: {u_type} (уверенность: {conf:.2f})")
                            self.current_unit = u_type
                            # Обновляем список ресурсов
                            if u_type == 'gamma' and 'coal' not in RESOURCES:
                                RESOURCES.append('coal')
                                # До загружаем уголь если появился
                                self.load_templates() # Перезагрузит шаблоны с учетом нового ресурса? 
                                # Надо догрузить конкретно уголь
                                for i in range(1, 4):
                                    path = os.path.join(ORES_DIR, f"ore-coal{i}.png")
                                    if os.path.exists(path):
                                        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                                        if img is not None: self.templates.setdefault('coal', []).append(img)
                                print("Юнит Гамма! Добавлен уголь в приоритеты.")
                        #else:
                            # Тихий режим, не спамим
                    else:
                        # Если раньше был юнит, а теперь нет - возможно меню или смерть
                        if self.current_unit is not None:
                            print("Юнит потерян (меню? смерть?). Жду...")
                            # self.current_unit = None # Не сбрасываем сразу, вдруг лаг
                            
                self.last_unit_check = current_time

            # Если юнит не определен, пропускаем цикл добычи
            if not self.current_unit:
                time.sleep(0.5)
                continue

            # 2. Логика добычи
            if self.is_mining:
                # Проверяем, не прошло ли время
                if current_time - self.mining_start_time > CONFIG['mining_duration']:
                    print("Добыча завершена (таймер).")
                    self.is_mining = False
                    self.current_target = None # Сброс цели
                    # Отпускаем клавиши на всякий случай
                    pyautogui.keyUp('space') 
                else:
                    # Можно добавить проверку: если анимация пропала раньше времени - стоп
                    pass
                time.sleep(0.1)
                continue

            # 3. Поиск ресурсов
            screen_color, offset = self.get_screen_region(hwnd)
            if screen_color is None:
                time.sleep(1)
                continue
            screen_gray = cv2.cvtColor(screen_color, cv2.COLOR_BGR2GRAY)
            
            best_res = None
            best_loc = None
            best_conf = 0
            best_dist = float('inf')
            
            center_x = screen_gray.shape[1] // 2
            center_y = screen_gray.shape[0] // 2
            
            # Приоритет: сначала загруженные ресурсы (copper, lead, coal)
            # Можно сортировать RESOURCES по расстоянию до ядра, но пока просто перебор
            for res_name in RESOURCES:
                if res_name not in self.templates or not self.templates[res_name]:
                    continue
                    
                loc, conf, size = self.find_best_match(screen_gray, self.templates[res_name], threshold=0.85)
                
                if loc:
                    lx, ly = loc
                    # Расстояние до центра экрана (чтобы копать то, что ближе к камере/ядру если ядро в центре)
                    dist = math.hypot(lx - center_x, ly - center_y)
                    
                    # Фильтр: игнорируем текущую цель, если она уже в процессе (но мы сбрасываем target после mining)
                    # Если мы не добываем, но цель та же - может быть мы стоим рядом?
                    # Главное: не переключаться на другую жилу, если текущая еще не докончена.
                    # Но так как is_mining=False, значит мы закончили.
                    
                    # Ищем самую близкую к центру
                    if dist < best_dist:
                        best_dist = dist
                        best_res = res_name
                        best_loc = loc
                        best_conf = conf
            
            if best_res and best_loc:
                bx, by = best_loc
                print(f"Найдено: {best_res} (дист: {best_dist:.1f}, ув: {best_conf:.2f})")
                
                # Движение к ресурсу (чтобы он стал в центре)
                # Смещение 0,0 - хотим чтобы ресурс был ровно в центре для клика
                moved = self.smart_move_to(bx, by)
                
                # Клик
                # Небольшая задержка после движения
                time.sleep(0.2)
                self.click(bx, by)
                print(f"Начата добыча {best_res}")
                
                self.is_mining = True
                self.mining_start_time = current_time
                self.current_target = best_loc
                
                # Зажимаем пробел или ЛКМ? В Mindustry обычно авто-добыча при наведении и зажатии ЛКМ
                # Или просто клик? Скрипт выше делает один клик.
                # Для непрерывной добычи нужно держать ЛКМ.
                # Эмулируем зажатие:
                hwnd = self.get_game_window()
                if hwnd:
                    rect = win32gui.GetWindowRect(hwnd)
                    abs_x = rect[0] + 8 + bx
                    abs_y = rect[1] + 30 + by
                    win32api.SetCursorPos((int(abs_x), int(abs_y)))
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    # Отпустим в цикле проверки таймера
            
            else:
                # Ничего не найдено - патрулирование? Или просто ждать
                # print("Ресурсы не найдены в кадре.")
                time.sleep(0.5)

if __name__ == "__main__":
    try:
        bot = MinBot()
        bot.run()
    except KeyboardInterrupt:
        print("\nОстановка бота.")
        # Отпускание клавиш при выходе
        pyautogui.keyUp('w')
        pyautogui.keyUp('a')
        pyautogui.keyUp('s')
        pyautogui.keyUp('d')
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
