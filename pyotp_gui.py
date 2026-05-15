import pyotp, clipboard, keyboard, time, traceback, multiprocessing, json, os, sys, uuid, ctypes, threading, win32api, win32con, win32gui, queue
from PySide6.QtGui import QIcon, QAction
from winotify import Notification
from PySide6.QtCore import QSize, Qt, QRect, QMargins
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QWidget, QVBoxLayout, QLineEdit, \
    QGridLayout, QScrollArea, QGroupBox, QLayout, QCheckBox, QHBoxLayout, QMessageBox, QSystemTrayIcon, QStyle, QMenu, \
    QDoubleSpinBox

Q_WINDOW:'TOTP_Window'
HOTKEY_MANAGER:'HotkeyManager'
PASTE_KEY = 'ctrl+v'
ENTER = 'enter'
TOTP_CONFIG_FILE_NAME = '.\\config\\totp_paster_config.json'
TOTP_LOG_FILE_NAME = '.\\log\\totp_paster.log'
TOTP_TRAY_ICON_FILE_NAME = '.\\ico\\totp_paster.ico'
ACTIVATE_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg="Please set new text",
    duration="short"
)
START_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg="TOTP Paster 시작",
    duration="short"
)
ERROR_MSG = ''
ERROR_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg=ERROR_MSG,
    duration="short"
)
CONFIG = dict()

Q_APP:QApplication
BUTTON_STATUS_CREATED = 0
BUTTON_STATUS_CHANGE = 1
BUTTON_STATUS_SAVE = 2
BUTTON_STATUS_ACTIVATED = 3
BUTTON_STATUS_INACTIVATED = 4
BUTTON_STATUS_STATELESS = 9

STORED_HOTKEY:list = [''] * 2
TRACK_ENTER:bool = False

USED_HOTKEY:set = set()
USED_SECRET:set = set()
# {
#     'pasters': {
#         'id-1': {
#             'id': 'id-1',
#             'hotkey': '',
#             'secret': '',
#             'is_active': True,
#         },
#         'id-2': {
#             'id': 'id-2',
#             'hotkey': '',
#             'secret': '',
#             'is_active': True,
#         }
#     },
#     'auto_fill': False
# }

################################################################################
############################## PRE-BUILT CLASSES  ##############################
################################################################################
WM_HOTKEY_REGISTER   = win32con.WM_USER + 1
WM_HOTKEY_UNREGISTER = win32con.WM_USER + 2
WM_HOTKEY_UNREGISTER_ALL = win32con.WM_USER + 3
class HotkeyManager:
    """
    RegisterHotKey + Message-Only Window 기반 전역 단축키 매니저.
    별도 스레드에서 메시지 루프를 실행하며, 다른 스레드에서 register/unregister 호출 가능.
    """

    # keyboard 라이브러리의 normalize_name 결과와 호환되는 modifier 매핑
    MOD_MAP = {
        'ctrl':    win32con.MOD_CONTROL,
        'control': win32con.MOD_CONTROL,
        'alt':     win32con.MOD_ALT,
        'shift':   win32con.MOD_SHIFT,
        'win':     win32con.MOD_WIN,
        'windows': win32con.MOD_WIN,
    }

    VK_MAP = {
        'f1':  win32con.VK_F1,  'f2':  win32con.VK_F2,  'f3':  win32con.VK_F3,
        'f4':  win32con.VK_F4,  'f5':  win32con.VK_F5,  'f6':  win32con.VK_F6,
        'f7':  win32con.VK_F7,  'f8':  win32con.VK_F8,  'f9':  win32con.VK_F9,
        'f10': win32con.VK_F10, 'f11': win32con.VK_F11, 'f12': win32con.VK_F12,
        'space':     win32con.VK_SPACE,
        'enter':     win32con.VK_RETURN,
        'tab':       win32con.VK_TAB,
        'escape':    win32con.VK_ESCAPE,
        'esc':       win32con.VK_ESCAPE,
        'backspace': win32con.VK_BACK,
        'delete':    win32con.VK_DELETE,
        'insert':    win32con.VK_INSERT,
        'home':      win32con.VK_HOME,
        'end':       win32con.VK_END,
        'page up':   win32con.VK_PRIOR,
        'page down': win32con.VK_NEXT,
        'up':    win32con.VK_UP,
        'down':  win32con.VK_DOWN,
        'left':  win32con.VK_LEFT,
        'right': win32con.VK_RIGHT,
        'num0':  win32con.VK_NUMPAD0, 'num1': win32con.VK_NUMPAD1,
        'num2':  win32con.VK_NUMPAD2, 'num3': win32con.VK_NUMPAD3,
        'num4':  win32con.VK_NUMPAD4, 'num5': win32con.VK_NUMPAD5,
        'num6':  win32con.VK_NUMPAD6, 'num7': win32con.VK_NUMPAD7,
        'num8':  win32con.VK_NUMPAD8, 'num9': win32con.VK_NUMPAD9,
    }

    _CLASS_NAME = 'HotkeyManagerWnd'

    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks: dict[int, callable] = {}    # hotkey_id  → callback
        self._hotkey_to_id: dict[str, int] = {}      # hotkey_str → hotkey_id
        self._id_counter = 1
        self._hwnd = None

        # register/unregister 결과를 호출 스레드에 돌려주기 위한 큐
        self._result_queue: queue.Queue = queue.Queue()

        # 루프 스레드에 전달할 등록 요청 임시 저장
        self._pending: dict = {}

        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._message_loop,
            name='HotkeyManagerThread',
            daemon=True
        )
        self._thread.start()

        # 윈도우 생성 완료까지 대기 (최대 3초)
        if not self._ready.wait(timeout=3):
            raise RuntimeError('HotkeyManager: 메시지 윈도우 생성 타임아웃')

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def register(self, hotkey_str: str, callback: callable) -> bool:
        """단축키 등록. 성공 시 True, 실패(파싱 오류/중복/OS 거부) 시 False."""
        modifiers, vk = self._parse_hotkey(hotkey_str)
        if vk == 0:
            return False

        with self._lock:
            if hotkey_str in self._hotkey_to_id:
                return False  # 이미 등록됨

            hotkey_id = self._id_counter
            self._id_counter += 1
            # 루프 스레드에서 꺼낼 수 있도록 임시 저장
            self._pending[hotkey_id] = (modifiers, vk, callback, hotkey_str)

        # RegisterHotKey 를 루프 ㅅ레드에서 실행하도록 요청
        win32api.PostMessage(self._hwnd, WM_HOTKEY_REGISTER, hotkey_id, 0)

        # 루프 스레드의 실행 결과 대기 (타임아웃 2초)
        try:
            success = self._result_queue.get(timeout=2)
        except queue.Empty:
            success = False

        return success


    def unregister(self, hotkey_str: str) -> None:
        """단축키 해제."""
        with self._lock:
            hotkey_id = self._hotkey_to_id.pop(hotkey_str, None)
            if hotkey_id is None:
                return

        # UnregisterHotKey 를 루프 스레드에서 실행하도록 요청
        win32api.PostMessage(self._hwnd, WM_HOTKEY_UNREGISTER, hotkey_id, 0)


    def unregister_all(self) -> None:
        """등록된 모든 단축키 해제."""
        win32api.PostMessage(self._hwnd, WM_HOTKEY_UNREGISTER_ALL, None, 0)

    def stop(self) -> None:
        """매니저 종료 (스레드 메시지 루프 종료)."""
        self.unregister_all()
        if self._hwnd:
            win32gui.PostMessage(self._hwnd, win32con.WM_QUIT, 0, 0)

    # ──────────────────────────────────────────────
    # 내부 구현
    # ──────────────────────────────────────────────

    def _message_loop(self) -> None:
        """별도 스레드에서 실행: Message-Only Window 생성 + 메시지 루프."""
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self._CLASS_NAME
        wc.lpfnWndProc = self._wnd_proc
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass  # 이미 등록된 경우 무시

        self._hwnd = win32gui.CreateWindowEx(
            0,
            self._CLASS_NAME,
            'HotkeyManager',
            0, 0, 0, 0, 0,
            win32con.HWND_MESSAGE,  # Message-Only Window
            0, 0, None
        )
        self._ready.set()

        # 메시지 루프: WM_HOTKEY 수신 시 _wnd_proc 호출
        win32gui.PumpMessages()

    def _wnd_proc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_HOTKEY_REGISTER:
            # register() 가 요청한 등록을 루프 스레드에서 실제 수행
            hotkey_id = wparam
            with self._lock:
                pending = self._pending.get(hotkey_id, None)

            if pending is None:
                self._result_queue.put(False)
                return 0

            modifiers, vk, callback, hotkey_str = pending
            ok = bool(ctypes.windll.user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk))

            if ok:
                with self._lock:
                    self._hotkey_to_id[hotkey_str] = hotkey_id
                    self._callbacks[hotkey_id] = callback
            else:
                err = ctypes.windll.kernel32.GetLastError()
                print(f'[HotkeyManager] RegisterHotKey 실패 - id={hotkey_id} err={err}')

            self._result_queue.put(ok)
            return 0

        if msg == WM_HOTKEY_UNREGISTER:
            # unregister()가 요청한 해제를 루프 스레드에서 실제 수행
            hotkey_id = wparam
            with self._lock:
                # id → str 역방향 탐색
                hotkey_str = next(
                    (k for k, v in self._hotkey_to_id.items() if v == hotkey_id), None
                )
                if hotkey_str:
                    self._hotkey_to_id.pop(hotkey_str, None)
                self._callbacks.pop(hotkey_id, None)

            ctypes.windll.user32.UnregisterHotKey(hwnd, hotkey_id)
            return 0

        if msg == WM_HOTKEY_UNREGISTER_ALL:
            with self._lock:
                ids = list(self._callbacks.keys())
                self._callbacks.clear()
                self._hotkey_to_id.clear()

            for hotkey_id in ids:
                ctypes.windll.user32.UnregisterHotKey(hwnd, hotkey_id)

        if msg == win32con.WM_HOTKEY:
            with self._lock:
                callback = self._callbacks.get(wparam)
            if callback:
                try:
                    callback()
                except Exception as e:
                    log_error(e)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _parse_hotkey(self, hotkey_str: str) -> tuple[int, int]:
        """
        'ctrl+alt+a' → (modifiers, vk_code)
        keyboard.normalize_name() 출력 형식과 호환됨.
        """
        parts = [p.strip().lower() for p in hotkey_str.split('+')]
        modifiers = win32con.MOD_NOREPEAT  # 키 반복 방지
        vk = 0
        for part in parts:
            if part in self.MOD_MAP:
                modifiers |= self.MOD_MAP[part]
            else:
                vk = self._key_to_vk(part)
        return modifiers, vk

    def _key_to_vk(self, key: str) -> int:
        if key in self.VK_MAP:
            return self.VK_MAP[key]
        # 단일 문자 (알파벳, 숫자 등)
        if len(key) == 1:
            result = ctypes.windll.user32.VkKeyScanW(ord(key))
            if result != -1:
                return result & 0xFF
        return 0  # 인식 불가



class Paster:
    totp = None
    _hotkey_manager: 'HotkeyManager' = None # 클래스 공유 매니저

    def __init__(self, template: dict):
        self.id:str = template['id']
        self.hotkey:str = template['hotkey']
        self.secret:str = None
        self.is_active:bool = template['is_active']
        if template['secret'] is not None:
            self.set_secret(template['secret'])


    @classmethod
    def set_hotkey_manager(cls, manager: 'HotkeyManager') -> None:
        cls._hotkey_manager = manager


    def set_secret(self, secret: str):
        self.secret = secret
        self.totp = pyotp.TOTP(self.secret)
        get_config('pasters')[self.id]['secret'] = secret


    def set_hotkey(self, hotkey: str):
        self.hotkey = hotkey
        get_config('pasters')[self.id]['hotkey'] = hotkey


    def switch_active(self, active: bool):
        self.is_active = active
        get_config('pasters')[self.id]['is_active'] = active


    def generate(self):
        if self.totp is not None and self.is_active:
            otp_value = self.totp.now()
            clipboard.copy(otp_value)
            paste_if_necessary()
            enter_if_necessary()


    def active_hotkey(self) -> bool:
        if not self._hotkey_manager or not self.hotkey:
            return False
        return self._hotkey_manager.register(self.hotkey, self.generate)


    def inactive_hotkey(self) -> None:
        if not self._hotkey_manager or not self.hotkey:
            return
        self._hotkey_manager.unregister(self.hotkey)


class TOTP_Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('OTP Paster')
        self.setFixedSize(QSize(400, 300))
        self.tray_icon = None
        widget_container = QWidget()

        otp_area = QScrollArea()
        otp_area.setGeometry(QRect(10, 10, 380, 280))
        otp_area.setWidgetResizable(True)
        otp_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        otp_area.setContentsMargins(QMargins(20, 10, 20, 10))

        scroll_widget_container = QWidget()

        otp_layout = QVBoxLayout(scroll_widget_container)
        otp_layout.setObjectName('totp-group-layout')
        otp_layout.setVerticalSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        for i, p in enumerate(get_config('pasters').values()):
            paster = Paster(p)
            group_box = TOTP_GroupBox(paster)
            otp_layout.addWidget(group_box)

        add_group_button = TOTP_Button('추가', otp_layout, BUTTON_STATUS_STATELESS, False)
        add_group_button.clicked.connect(self.add_new_group)
        add_group_button.setAutoFillBackground(True)
        otp_layout.addWidget(add_group_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        otp_area.setWidget(scroll_widget_container)

        layout = QVBoxLayout()
        layout.addWidget(otp_area)

        bottom_layout_wrapper = QWidget()
        bottom_layout = QHBoxLayout(bottom_layout_wrapper)

        auto_fill_check_box = QCheckBox('자동 붙여넣기')
        auto_enter_check_box = QCheckBox('자동 엔터')

        auto_fill_check_box.setProperty('child-checkbox', auto_enter_check_box)
        auto_fill_check_box.setChecked(is_true(get_config('auto_fill')))
        auto_fill_check_box.clicked.connect(self.auto_fill_check)
        bottom_layout.addWidget(auto_fill_check_box)

        auto_enter_check_box.setChecked(is_true(get_config('auto_enter')))
        auto_enter_check_box.clicked.connect(self.auto_enter_check)
        bottom_layout.addWidget(auto_enter_check_box)
        if not is_true(get_config('auto_fill')): auto_enter_check_box.setDisabled(True)

        paste_delay = QDoubleSpinBox()
        paste_delay.setRange(0,1)
        paste_delay.setSingleStep(0.1)
        paste_delay.setValue(float(get_config('paste_delay')))
        paste_delay.valueChanged.connect(self.delay_change)
        bottom_layout.addWidget(paste_delay)
        bottom_layout.addWidget(QLabel('붙여넣기 지연시간(초)'))

        layout.addWidget(bottom_layout_wrapper)

        save_config_button = QPushButton('저장')
        save_config_button.clicked.connect(self.save_config)
        layout.addWidget(save_config_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        widget_container.setLayout(layout)

        self.setCentralWidget(widget_container)
        self.setup_tray_icon()


    def auto_fill_check(self):
        sender = self.sender()
        auto_enter_checkbox = sender.property('child-checkbox')
        if sender.isChecked():
            set_config('auto_fill', 'True')
            auto_enter_checkbox.setDisabled(False)
        else:
            set_config('auto_fill', 'False')
            auto_enter_checkbox.setChecked(False)
            auto_enter_checkbox.setDisabled(True)
            set_config('auto_enter', 'False')


    def auto_enter_check(self):
        sender = self.sender()
        if sender.isChecked():
            set_config('auto_enter', 'True')
        else:
            set_config('auto_enter', 'False')


    def add_new_group(self):
        sender = self.sender()
        rand_id = uuid.uuid1().__str__()
        group_dict = {
            'id': rand_id,
            'secret': '',
            'hotkey': '',
            'is_active': False,
        }
        get_config('pasters')[rand_id] = group_dict
        sender.target.removeWidget(sender)
        sender.target.addWidget(TOTP_GroupBox(Paster(group_dict), True))
        sender.target.addWidget(sender)


    def setup_tray_icon(self):
        global Q_APP, TOTP_TRAY_ICON_FILE_NAME
        self.tray_icon = QSystemTrayIcon(self)

        icon = self.windowIcon()
        if icon.isNull():
            icon = Q_APP.instance().windowIcon()
        if icon.isNull():
            icon = QIcon(TOTP_TRAY_ICON_FILE_NAME)
        if icon.isNull():
            icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()

        show_action = QAction('열기', self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        reload_action = QAction('새로고침', self)
        reload_action.triggered.connect(reload_window)
        tray_menu.addAction(reload_action)

        quit_action = QAction('종료', self)
        quit_action.triggered.connect(quit_application)
        tray_menu.addAction(quit_action)


        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip('TOTP Paster')

        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()


    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("백라운드 실행", "백그라운드 실행", QSystemTrayIcon.MessageIcon.Information, 1500)


    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()


    def show_window(self):
        self.show()
        self.activateWindow()

    def save_config(self):
        sender = self.sender()
        try:
            global TOTP_CONFIG_FILE_NAME
            with open(TOTP_CONFIG_FILE_NAME, mode='w') as config:
                json.dump(get_config(None), config, indent=4)
            QMessageBox().question(sender.parent(), '저장 완료', '설정이 저장 되었습니다.', QMessageBox.StandardButton.Ok)
        except Exception as e:
            QMessageBox().question(sender.parent(), '저장 오류', '설정 저장 중 오류가 발생했습니다.', QMessageBox.StandardButton.Ok)
            log_error(e)


    def delay_change(self):
        sender = self.sender()
        changed = sender.text()
        try:
            float(changed)
            set_config('paste_delay', changed)
        except Exception as e:
            sender.setText('')
            QMessageBox.question(sender.parent(), '숫자 형식 오류', '올바른 숫자를 입력해주세요.', QMessageBox.StandardButton.Ok)
            log_error(e)


class TOTP_Button(QPushButton):
    def __init__(self, text, target, status, disable) -> None:
        super().__init__(text)
        self.target = target
        self.status = status
        self.setDisabled(disable)


class TOTP_GroupBox(QGroupBox):
    paster = None
    def __init__(self, paster : Paster, is_new: bool = False):
        super().__init__()
        self.paster = paster
        self.setObjectName(f"wid-group-{paster.id}")
        if is_new:
            grid_layout = self.create_grid_layout(paster, 'new')
        else:
            if paster.is_active:
                grid_layout = self.create_grid_layout(paster, 'active')
            else:
                grid_layout = self.create_grid_layout(paster, 'inactive')

        self.setLayout(grid_layout)


    def create_grid_layout(self, paster, type) -> QGridLayout:
        grid_layout = QGridLayout()

        secret_key_label = "Secret Key : "
        hotkey_label = "Hot Key : "
        if type == 'new':
            grid_layout.addWidget(QLabel(secret_key_label), 0, 0)
            secret_input = QLineEdit(self.paster.secret, readOnly=True)
            secret_input.setObjectName(f"wid-sc-input-{paster.id}")
            grid_layout.addWidget(secret_input, 0, 1)

            secret_change_button = TOTP_Button('입력', secret_input, BUTTON_STATUS_CREATED, False)
            secret_change_button.clicked.connect(self.change_secret)
            secret_change_button.setObjectName(f"wid-sc-button-{paster.id}")
            grid_layout.addWidget(secret_change_button, 0, 2)

            remove_button = TOTP_Button('삭제', self, BUTTON_STATUS_STATELESS, False)
            remove_button.clicked.connect(self.remove_self)
            remove_button.setObjectName(f"wid-remove-{paster.id}")
            grid_layout.addWidget(remove_button, 0, 3)

            grid_layout.addWidget(QLabel(hotkey_label), 1, 0)
            hotkey_input = QLineEdit(self.paster.hotkey, readOnly=True)
            hotkey_input.setObjectName(f"wid-hk-input-{paster.id}")
            grid_layout.addWidget(hotkey_input, 1, 1)

            hotkey_change_button = TOTP_Button('입력', hotkey_input, BUTTON_STATUS_CREATED, True)
            hotkey_change_button.clicked.connect(self.replace_hotkey)
            hotkey_change_button.setObjectName(f"wid-hk-button-{paster.id}")
            grid_layout.addWidget(hotkey_change_button, 1, 2)

            activate_button = TOTP_Button('켜기', None, BUTTON_STATUS_INACTIVATED, False)
            activate_button.clicked.connect(self.toggle_activate)
            activate_button.setObjectName(f"wid-activate-{paster.id}")
            grid_layout.addWidget(activate_button, 1, 3)
        elif type == 'active':
            grid_layout.addWidget(QLabel(secret_key_label), 0, 0)

            secret_input = QLineEdit(self.paster.secret, readOnly=True)
            secret_input.setObjectName(f"wid-sc-input-{paster.id}")
            secret_input.setDisabled(True)
            grid_layout.addWidget(secret_input, 0, 1)

            secret_change_button = TOTP_Button('변경', secret_input, BUTTON_STATUS_CHANGE, True)
            secret_change_button.clicked.connect(self.change_secret)
            secret_change_button.setObjectName(f"wid-sc-button-{paster.id}")
            grid_layout.addWidget(secret_change_button, 0, 2)

            remove_button = TOTP_Button('삭제', self, BUTTON_STATUS_STATELESS, True)
            remove_button.clicked.connect(self.remove_self)
            remove_button.setObjectName(f"wid-remove-{paster.id}")
            grid_layout.addWidget(remove_button, 0, 3)

            grid_layout.addWidget(QLabel(hotkey_label), 1, 0)

            hotkey_input = QLineEdit(self.paster.hotkey, readOnly=True)
            hotkey_input.setObjectName(f"wid-hk-input-{paster.id}")
            hotkey_input.setDisabled(True)
            grid_layout.addWidget(hotkey_input, 1, 1)

            hotkey_change_button = TOTP_Button('변경', hotkey_input, BUTTON_STATUS_CHANGE, True)
            hotkey_change_button.clicked.connect(self.replace_hotkey)
            hotkey_change_button.setObjectName(f"wid-hk-button-{paster.id}")
            grid_layout.addWidget(hotkey_change_button, 1, 2)

            activate_button = TOTP_Button('끄기', None, BUTTON_STATUS_ACTIVATED, False)
            activate_button.clicked.connect(self.toggle_activate)
            activate_button.setObjectName(f"wid-activate-{paster.id}")
            grid_layout.addWidget(activate_button, 1, 3)
        else:
            grid_layout.addWidget(QLabel(secret_key_label), 0, 0)
            secret_input = QLineEdit(self.paster.secret, readOnly=True)
            secret_input.setObjectName(f"wid-sc-input-{paster.id}")
            grid_layout.addWidget(secret_input, 0, 1)

            secret_change_button = TOTP_Button('변경', secret_input, BUTTON_STATUS_CREATED, False)
            secret_change_button.clicked.connect(self.change_secret)
            secret_change_button.setObjectName(f"wid-sc-button-{paster.id}")
            grid_layout.addWidget(secret_change_button, 0, 2)

            remove_button = TOTP_Button('삭제', self, BUTTON_STATUS_STATELESS, False)
            remove_button.clicked.connect(self.remove_self)
            remove_button.setObjectName(f"wid-remove-{paster.id}")
            grid_layout.addWidget(remove_button, 0, 3)

            grid_layout.addWidget(QLabel(hotkey_label), 1, 0)
            hotkey_input = QLineEdit(self.paster.hotkey, readOnly=True)
            hotkey_input.setObjectName(f"wid-hk-input-{paster.id}")
            grid_layout.addWidget(hotkey_input, 1, 1)

            hotkey_change_button = TOTP_Button('변경', hotkey_input, BUTTON_STATUS_CREATED, True)
            hotkey_change_button.clicked.connect(self.replace_hotkey)
            hotkey_change_button.setObjectName(f"wid-hk-button-{paster.id}")
            grid_layout.addWidget(hotkey_change_button, 1, 2)

            activate_button = TOTP_Button('켜기', None, BUTTON_STATUS_INACTIVATED, False)
            activate_button.clicked.connect(self.toggle_activate)
            activate_button.setObjectName(f"wid-activate-{paster.id}")
            grid_layout.addWidget(activate_button, 1, 3)
        return grid_layout


    def change_secret(self):
        sender = self.sender()
        global CONFIG, USED_SECRET
        get_secret = sender.target.text()
        if sender.status == BUTTON_STATUS_CHANGE:
            USED_SECRET.discard(get_secret)
            sender.target.setReadOnly(False)
            sender.target.setText('')
            sender.status = BUTTON_STATUS_SAVE
            sender.setText('저장')
            self.switch_other_widgets(True, v_type=QPushButton, v_target='sc')
        else:
            if not USED_SECRET.__contains__(get_secret):
                USED_SECRET.add(get_secret)
                sender.target.setReadOnly(True)
                sender.status = BUTTON_STATUS_CHANGE
                self.paster.set_secret(get_secret)
                sender.setText('변경')
                self.switch_other_widgets(False, v_type=QPushButton, v_target='sc')
            else:
                sender.target.setText('OTP 키 중복')


    def remove_self(self):
        sender = self.sender()

        pasters = get_config('pasters')
        paster_self = pasters[self.paster.id]
        USED_HOTKEY.discard(paster_self['hotkey'])
        USED_SECRET.discard(paster_self['secret'])
        pasters.pop(self.paster.id)
        sender.target.setParent(None)
        sender.target.deleteLater()


    def replace_hotkey(self):
        sender = self.sender()
        global Q_WINDOW, STORED_HOTKEY, USED_HOTKEY
        if sender.status == BUTTON_STATUS_CHANGE:
            USED_HOTKEY.discard(sender.target.text())
            sender.setText('저장')
            sender.target.setText('')
            keyboard.hook(track_keypress)
            sender.status = BUTTON_STATUS_SAVE
            self.switch_other_widgets(True, v_type=QPushButton, v_target='hk')
        else:
            joined_hotkey = keyboard.normalize_name('+'.join(STORED_HOTKEY))
            if STORED_HOTKEY[0] == '' or STORED_HOTKEY[1] == '':
                sender.target.setText('유효하지 않은 단축키')
            elif not USED_HOTKEY.__contains__(joined_hotkey):
                USED_HOTKEY.add(joined_hotkey)
                sender.setText('변경')
                sender.target.setText(joined_hotkey)
                keyboard.unhook(track_keypress)
                sender.status = BUTTON_STATUS_CHANGE
                self.paster.set_hotkey(joined_hotkey)
                STORED_HOTKEY = [''] * 2
                self.switch_other_widgets(False, v_type=QPushButton, v_target='hk')
            else:
                sender.target.setText('단축키 중복')


    def toggle_activate(self):
        sender = self.sender()
        global ACTIVATE_TOAST, CONFIG
        if sender.status == BUTTON_STATUS_ACTIVATED:
            self.paster.switch_active(False)
            self.paster.inactive_hotkey()
            sender.status = BUTTON_STATUS_INACTIVATED
            sender.setText('켜기')
            ACTIVATE_TOAST.msg = f"단축키 비활성화 : {self.paster.hotkey}"
            self.switch_widgets(False)
        else:
            completed:list = [False] * 2
            for w in Q_WINDOW.findChildren(TOTP_Button):
                if w.objectName() == f'wid-sc-button-{self.paster.id}':
                    completed[0] = w.status == BUTTON_STATUS_CHANGE
                elif w.objectName() == f'wid-hk-button-{self.paster.id}':
                    completed[1] = w.status == BUTTON_STATUS_CHANGE
            if all(completed):
                self.paster.switch_active(True)
                self.paster.active_hotkey()
                sender.status = BUTTON_STATUS_ACTIVATED
                sender.setText('끄기')
                ACTIVATE_TOAST.msg = f"단축키 활성화 : {self.paster.hotkey}"
                self.switch_widgets(True)
            else:
                QMessageBox().question(sender.parent(), '등록 오류', 'Secret 과 Hotkey 를 모두 등록해주세요.', QMessageBox.StandardButton.Ok)
        ACTIVATE_TOAST.show()


    def switch_widgets(self, flag:bool, v_id:str = None, v_type = QWidget):
        global Q_WINDOW
        if not v_id: v_id = self.paster.id
        for w in Q_WINDOW.findChildren(v_type):
            if ((w.objectName().__contains__('sc') or w.objectName().__contains__('hk') or w.objectName().__contains__('remove'))
                    and w.objectName().__contains__(v_id)):
                w.setDisabled(flag)


    def switch_other_widgets(self, flag:bool, v_id:str = None, v_type = QWidget, v_target:str = None):
        global Q_WINDOW
        if not v_id: v_id = self.paster.id
        for w in Q_WINDOW.findChildren(v_type):
            if not v_target:
                if ((w.objectName().__contains__('sc') or w.objectName().__contains__('hk'))
                        and not w.objectName().__contains__(v_id)):
                    w.setDisabled(flag)
            else:
                if not (w.objectName().__contains__(v_target) and w.objectName().__contains__(v_id)):
                    w.setDisabled(flag)

################################################################################
############################### STATIC FUNCTIONS ###############################
################################################################################
def paste_if_necessary():
    if is_true(get_config('auto_fill')):
        time.sleep(float(get_config('paste_delay')))
        keyboard.press_and_release(PASTE_KEY)


def enter_if_necessary():
    if is_true(get_config('auto_enter')):
        time.sleep(0.1) # 고정
        keyboard.press_and_release(ENTER)


def track_keypress(event):
    global STORED_HOTKEY
    name = event.name
    if keyboard.is_modifier(event.name):
        STORED_HOTKEY[0] = name
    else:
        STORED_HOTKEY[1] = name


def track_enter(event):
    name = event.name
    global TRACK_ENTER
    TRACK_ENTER = True if name == 'enter' else False


def load_config():
    global USED_HOTKEY, USED_SECRET
    if os.path.exists(TOTP_CONFIG_FILE_NAME):
        with open(TOTP_CONFIG_FILE_NAME, mode ='r+') as config:
            cfg = config.readlines()
            if len(cfg) == 0:
                set_config('pasters', dict())
                set_config('auto_fill', 'False')
                set_config('paste_delay', '0.3')
                set_config('auto_enter', 'False')
                json.dump(get_config(None), config, indent=4)
            else:
                set_config(None, json.loads(' '.join(cfg)))
    else:
        with open(TOTP_CONFIG_FILE_NAME, mode ='w') as config:
            set_config('pasters', dict())
            set_config('auto_fill', 'False')
            set_config('paste_delay', '0.3')
            set_config('auto_enter', 'False')
            json.dump(get_config(None), config, indent = 4)


def register_all_hotkey_with_config():
    global USED_HOTKEY, USED_SECRET
    for p in get_config('pasters').values():
        paster = Paster(p)
        if paster.is_active:
            paster.active_hotkey()
            USED_HOTKEY.add(paster.hotkey)
            USED_SECRET.add(paster.secret)


def get_config(key):
    global CONFIG
    if key is None: return CONFIG
    if key in CONFIG:
        return CONFIG[key]
    else:
        return None


def set_config(key, value):
    global CONFIG
    if key is None:
        CONFIG = value
    else:
        CONFIG[key] = value


def is_true(value: str):
    return value == 'True'


def main():
    global Q_WINDOW, Q_APP, HOTKEY_MANAGER
    HOTKEY_MANAGER = HotkeyManager()
    Paster.set_hotkey_manager(HOTKEY_MANAGER)
    register_all_hotkey_with_config()
    START_TOAST.show()
    Q_APP = QApplication(sys.argv)
    Q_APP.setQuitOnLastWindowClosed(False)
    Q_WINDOW = init_window()
    sys.exit(Q_APP.exec())


def init_window():
    window = TOTP_Window()
    window.show()
    return window


def quit_application():
    global Q_APP, Q_WINDOW, HOTKEY_MANAGER
    keyboard.unhook_all()
    HOTKEY_MANAGER.stop()
    if Q_WINDOW and Q_WINDOW.tray_icon:
        Q_WINDOW.tray_icon.hide()
    Q_APP.quit()


def reload_window():
    global TOTP_CONFIG_FILE_NAME, HOTKEY_MANAGER
    keyboard.unhook_all()
    HOTKEY_MANAGER.unregister_all()
    with open(TOTP_CONFIG_FILE_NAME, mode='w') as config:
        json.dump(get_config(None), config, indent=4)
    load_config()


def log_error(err: Exception):
    global ERROR_MSG
    ERROR_MSG = str(err)[:100]
    ERROR_TOAST.msg = ERROR_MSG
    ERROR_TOAST.show()
    if os.path.exists(TOTP_CONFIG_FILE_NAME):
        with open(TOTP_LOG_FILE_NAME, 'a') as f:
            f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(traceback.format_exc())
    else:
        with open(TOTP_LOG_FILE_NAME, 'w') as f:
            f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(traceback.format_exc())


def check_and_init_directories():
    if not os.path.exists('.\\config'):
        os.mkdir('.\\config')
    if not os.path.exists('.\\log'):
        os.mkdir('.\\log')
    if not os.path.exists('.\\ico'):
        os.mkdir('.\\ico')


if __name__ == "__main__":
    multiprocessing.freeze_support()
    check_and_init_directories()
    load_config()
    try:
        main()
    except Exception as e:
        log_error(e)
        quit_application()

# pyinstaller --onefile --noconsole --name "TotpPaster" --icon .\ico\totp_paster.ico --hidden-import=multiprocessing --hidden-import=keyboard .\pyotp_gui.py