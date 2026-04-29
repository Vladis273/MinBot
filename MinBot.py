import cv2
import numpy as np
import pyautogui
import time
import os
import sys
from ctypes import windll
from collections import deque

# --- КОНФИГУРАЦИЯ ---
CONFIG = {
    'game_path': r"C:\Users\123\Desktop\MinBot",  # Путь к папке проекта
    'confidence_threshold': 0.85,  # Порог уверенности для поиска
    'unit_check_interval': 5.0,    # Проверять юнит раз в 5 секунд
    'mining_duration': 2.0,        # Сколько секунд копать
    'move_step': 10,               # Шаг движения пикселей
    'move_delay': 0.05,            # Задержка между нажатиями WASD
    'debug': True                  # Вывод логов
}

# Пути к спрайтам (относительно game_path или абсолютные)
# Используем абсолютные пути из вашего лога для надежности
BASE_PATH = r"C:\Users\123\Desktop\MinBot"
SPRITES_DIR = os.path.join(BASE_PATH, 'data', 'sprites')
ORES_DIR = os.path.join(SPRITES_DIR, 'blocks', 'environment')
UNITS_DIR = os.path.join(SPRITES_DIR, 'units')

class MinBot:
    def __init__(self):
        self.templates = {'copper': [], 'lead': [], 'coal': []}
        self.unit_templates = {'alpha': [], 'beta': [], 'gamma': []}
        self.current_unit = None
        self.last_unit_check = 0
        self.current_target = None # Координаты текущей цели (x, y)
        self.is_mining = False
        self.mining_start_time = 0
        
        # Настройка DPI awareness для Windows
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.load_templates()
        
    def load_templates(self):
        """Загрузка шаблонов руд и юнитов"""
        print(f"Путь к рудам: {ORES_DIR}")
        print(f"Путь к юнитам: {UNITS_DIR}")

        # Загрузка руд
        resources = ['copper', 'lead']
        for res in resources:
            count = 0
            for i in range(1, 4): # ore-copper1.png ... ore-copper3.png
                path = os.path.join(ORES_DIR, f"ore-{res}{i}.png")
                if os.path.exists(path):
                    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        self.templates[res].append(img)
                        count += 1
            print(f"Загружено {count} шаблонов для {res}")

        # Загрузка юнитов
        units = ['alpha', 'beta', 'gamma']
        for unit in units:
            count = 0
            # Пробуем разные варианты именования, если нужно
            patterns = [f"{unit}.png", f"{unit}1.png", f"unit-{unit}.png"]
            for pattern in patterns:
                path = os.path.join(UNITS_DIR, pattern)
                if os.path.exists(path):
                    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        self.unit_templates[unit].append(img)
                        count += 1
            # Если не нашли по конкретным именам, ищем все файлы с именем юнита
            if count == 0 and os.path.exists(UNITS_DIR):
                for f in os.listdir(UNITS_DIR):
                    if unit in f.lower() and f.endswith('.png'):
                        path = os.path.join(UNITS_DIR, f)
                        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                        if img is not None:
                            self.unit_templates[unit].append(img)
                            count += 1
            
            print(f"Загружено {count} шаблонов для юнита {unit}")

    def get_screen_grab(self):
        """Делает скриншот экрана"""
        screenshot = pyautogui.screenshot()
        frame = np.array(screenshot)
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def find_best_match(self, templates, threshold=0.8):
        """Ищет лучшее совпадение среди списка шаблонов"""
        screen = self.get_screen_grab()
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        
        best_val = 0
        best_loc = None
        best_template_name = ""

        for name, tmpl_list in templates.items():
            for i, tmpl in enumerate(tmpl_list):
                if screen_gray.shape[0] < tmpl.shape[0] or screen_gray.shape[1] < tmpl.shape[1]:
                    continue
                    
                res = cv2.matchTemplate(screen_gray, tmpl, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                
                if max_val > best_val and max_val >= threshold:
                    best_val = max_val
                    best_loc = max_loc
                    best_template_name = name

        if best_loc:
            h, w = tmpl.shape # Размеры последнего использованного шаблона (приблизительно)
            center_x = int(best_loc[0] + w / 2)
            center_y = int(best_loc[1] + h / 2)
            return {'name': best_template_name, 'point': (center_x, center_y), 'confidence': best_val}
        
        return None

    def detect_unit(self):
        """Определяет текущего юнита с кэшированием"""
        current_time = time.time()
        
        # Если прошло мало времени с последней проверки, возвращаем закэшированное
        if current_time - self.last_unit_check < CONFIG['unit_check_interval']:
            return self.current_unit

        # Иначе сканируем
        detected = self.find_best_match(self.unit_templates, threshold=0.90) # Высокий порог для юнита
        
        if detected:
            old_unit = self.current_unit
            self.current_unit = detected['name']
            self.last_unit_check = current_time
            
            if old_unit != self.current_unit:
                print(f"Обнаружен новый юнит: {self.current_unit} (уверенность: {detected['confidence']:.2f})")
                
                # Логика доступности угля
                if self.current_unit == 'gamma':
                    if not self.templates['coal']: # Загрузить уголь если еще не загружен
                        self.load_coal_templates()
                    print("Юнит Gamma обнаружен. Уголь доступен.")
                else:
                    print(f"Юнит {self.current_unit.capitalize()} обнаружен. Уголь не доступен.")
            return self.current_unit
        else:
            # Если не нашли юнита, но был найден ранее, не сбрасываем сразу, чтобы избежать мерцания
            # Но если прошло много времени, можно сбросить
            if current_time - self.last_unit_check > CONFIG['unit_check_interval'] * 2:
                print("Не удалось обнаружить юнита на экране.")
                self.current_unit = None
            return self.current_unit

    def load_coal_templates(self):
        """Динамическая загрузка угля"""
        count = 0
        for i in range(1, 4):
            path = os.path.join(ORES_DIR, f"ore-coal{i}.png")
            if os.path.exists(path):
                img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.templates['coal'].append(img)
                    count += 1
        if count > 0:
            print(f"Загружено {count} шаблонов для coal")

    def move_to(self, target_x, target_y, tolerance=50):
        """Движение к точке с помощью WASD"""
        # Получаем центр экрана
        screen_w, screen_h = pyautogui.size()
        center_x, center_y = screen_w // 2, screen_h // 2
        
        dx = target_x - center_x
        dy = target_y - center_y
        
        if abs(dx) < tolerance and abs(dy) < tolerance:
            return True # Мы рядом

        # Нормализация направления
        moved = False
        if abs(dx) > tolerance:
            if dx > 0:
                pyautogui.keyDown('d')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('d')
            else:
                pyautogui.keyDown('a')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('a')
            moved = True
            
        if abs(dy) > tolerance:
            if dy > 0:
                pyautogui.keyDown('s')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('s')
            else:
                pyautogui.keyDown('w')
                time.sleep(CONFIG['move_delay'])
                pyautogui.keyUp('w')
            moved = True
            
        time.sleep(0.05) # Небольшая пауза между циклами движения
        return False

    def check_mining_animation(self, point):
        """
        Проверяет, началась ли анимация добычи.
        Делает два снимка с интервалом и сравнивает гистограмму области.
        """
        x, y = point
        region_size = 60 # Область вокруг центра клика
        left = max(0, x - region_size)
        top = max(0, y - region_size)
        right = left + region_size * 2
        bottom = top + region_size * 2
        
        # Снимок 1
        s1 = pyautogui.screenshot(region=(left, top, right-left, bottom-top))
        img1 = np.array(s1)
        hist1 = cv2.calcHist([img1], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        
        time.sleep(0.3) # Ждем немного, чтобы анимация проявилась
        
        # Снимок 2
        s2 = pyautogui.screenshot(region=(left, top, right-left, bottom-top))
        img2 = np.array(s2)
        hist2 = cv2.calcHist([img2], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        
        # Сравнение гистограмм (корреляция)
        score = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
        
        # Если картинка сильно изменилась (score низкий), значит анимация идет
        if score < 0.85: 
            return True
        return False

    def run(self):
        print("Бот запущен. Переключись в свою игру на основном мониторе.")
        time.sleep(3) # Даем время на переключение
        
        while True:
            try:
                # 1. Определяем юнит (с кэшированием)
                self.detect_unit()
                
                # Если юнит не определен, пропускаем цикл, чтобы не тыкать вслепую
                if not self.current_unit:
                    time.sleep(1)
                    continue

                # 2. Логика завершения добычи
                if self.is_mining:
                    if time.time() - self.mining_start_time > CONFIG['mining_duration']:
                        # Прекращаем добычу (отпускаем кнопку мыши на всякий случай)
                        pyautogui.mouseUp(button='left')
                        self.is_mining = False
                        self.current_target = None # Сбрасываем цель, чтобы искать новую
                        print("Добыча завершена. Поиск новой цели...")
                    else:
                        # Продолжаем ждать
                        time.sleep(0.1)
                        continue

                # 3. Поиск ресурсов
                # Формируем список активных ресурсов
                active_resources = ['copper', 'lead']
                if self.current_unit == 'gamma':
                    active_resources.append('coal')
                
                # Создаем временный словарь шаблонов только для активных ресурсов
                active_templates = {k: v for k, v in self.templates.items() if k in active_resources and v}
                
                if not active_templates:
                    print("Нет доступных шаблонов ресурсов!")
                    time.sleep(2)
                    continue

                found = self.find_best_match(active_templates, threshold=CONFIG['confidence_threshold'])
                
                if found:
                    # Исключаем текущую цель, если мы уже её копьем (защита от переключения)
                    if self.current_target and self.current_target == found['point']:
                        # Мы всё ещё смотрим на ту же цель, но анимации нет? 
                        # Возможно, ресурс кончился или баг. Сбросим цель.
                        self.current_target = None
                        continue

                    print(f"Найдено: {found['name']} в {found['point']} (уверенность: {found['confidence']:.2f})")
                    
                    # 4. Движение к ресурсу
                    # Пытаемся приблизиться, пока не окажемся достаточно близко
                    # Ограничим количество шагов движения, чтобы не застрять навсегда
                    steps = 0
                    while steps < 20: 
                        if self.move_to(found['point'][0], found['point'][1]):
                            break
                        steps += 1
                    
                    # 5. Начало добычи
                    pyautogui.click(found['point'][0], found['point'][1], button='left')
                    time.sleep(0.1)
                    pyautogui.click(found['point'][0], found['point'][1], button='left') # Двойной клик для надежности
                    
                    # Проверяем, началась ли анимация
                    if self.check_mining_animation(found['point']):
                        self.is_mining = True
                        self.mining_start_time = time.time()
                        self.current_target = found['point']
                        print("Анимация добычи подтверждена. Ждем завершения...")
                    else:
                        print("Анимация не обнаружена. Возможно, это не руда или ошибка. Пропуск.")
                        # Не ставим цель, чтобы в следующем цикле выбрать другую
                else:
                    # Ресурсы не найдены, можно добавить логику полета к ядру или патрулирования
                    # Сейчас просто ждем
                    time.sleep(0.5)

            except KeyboardInterrupt:
                print("Остановка бота.")
                break
            except Exception as e:
                print(f"Ошибка: {e}")
                time.sleep(1)

if __name__ == "__main__":
    bot = MinBot()
    bot.run()
