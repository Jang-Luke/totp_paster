import pyotp, clipboard, keyboard, time, traceback, multiprocessing, json, os, sys, uuid
from PySide6.QtGui import QIcon, QAction
from winotify import Notification
from PySide6.QtCore import QSize, Qt, QRect, QMargins
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QWidget, QVBoxLayout, QLineEdit, \
    QGridLayout, QScrollArea, QGroupBox, QLayout, QCheckBox, QHBoxLayout, QMessageBox, QSystemTrayIcon, QStyle, QMenu, \
    QDoubleSpinBox

PASTE_KEY = 'ctrl+v'
ENTER = 'enter'
TOTP_CONFIG_FILE_NAME = '.\\config\\totp_paster_config.json'
TOTP_LOG_FILE_NAME = '.\\log\\totp_paster.log'
TOTP_TRAY_ICON_FILE_NAME = '.\\ico\\totp_paster.ico'
COPY_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg="OTP 가 클립보드에 복사되었습니다.",
    duration="short"
)
ACTIVATE_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg="Please set new text",
    duration="short"
)
SAVE_CONFIG_TOAST = Notification(
    app_id="TOTP Paster",
    title="TOTP Paster",
    msg="설정이 저장되었습니다.",
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
BUTTON_STATUS_CHANGE = 1
BUTTON_STATUS_SAVE = 2
BUTTON_STATUS_ACTIVATED = 3
BUTTON_STATUS_INACTIVATED = 4

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
class Paster:
    totp = None
    def __init__(self, template: dict):
        self.id:str = template['id']
        self.hotkey:str = template['hotkey']
        self.secret:str = None
        self.is_active:bool = template['is_active']
        if template['secret'] is not None:
            self.set_secret(template['secret'])


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
            COPY_TOAST.show()


    def active_hotkey(self):
        keyboard.add_hotkey(self.hotkey, self.generate)


    def inactive_hotkey(self):
        keyboard.remove_hotkey(self.hotkey)


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
            # otp_area.addScrollBarWidget(group_box, Qt.AlignmentFlag.AlignTop)
            otp_layout.addWidget(group_box)

        add_group_button = TOTP_Button('추가', otp_layout)
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
    def __init__(self, text, target, status = BUTTON_STATUS_CHANGE) -> None:
        super().__init__(text)
        self.target = target
        self.status = status


class TOTP_GroupBox(QGroupBox):
    paster = None
    def __init__(self, paster : Paster, is_new: bool = False):
        super().__init__()
        self.paster = paster
        self.setObjectName(f"wid-group-{paster.id}")
        grid_layout = QGridLayout()

        grid_layout.addWidget(QLabel("Secret Key : "), 0, 0)

        secret_input = QLineEdit(self.paster.secret, readOnly=True)
        secret_input.setObjectName(f"wid-sc-input-{paster.id}")
        grid_layout.addWidget(secret_input, 0, 1)

        secret_change_button = TOTP_Button('변경', secret_input, BUTTON_STATUS_CHANGE)
        secret_change_button.clicked.connect(self.change_secret)
        secret_change_button.setObjectName(f"wid-sc-button-{paster.id}")
        grid_layout.addWidget(secret_change_button, 0, 2)

        remove_button = TOTP_Button('삭제', self, BUTTON_STATUS_CHANGE)
        remove_button.clicked.connect(self.remove_self)
        remove_button.setObjectName(f"wid-remove-{paster.id}")
        grid_layout.addWidget(remove_button, 0, 3)

        grid_layout.addWidget(QLabel("Hot Key : "), 1, 0)

        hotkey_input = QLineEdit(self.paster.hotkey, readOnly=True)
        hotkey_input.setObjectName(f"wid-hk-input-{paster.id}")
        grid_layout.addWidget(hotkey_input, 1, 1)

        hotkey_change_button = TOTP_Button('변경', hotkey_input, BUTTON_STATUS_CHANGE)
        hotkey_change_button.clicked.connect(self.replace_hotkey)
        hotkey_change_button.setObjectName(f"wid-hk-button-{paster.id}")
        grid_layout.addWidget(hotkey_change_button, 1, 2)

        activate_button = TOTP_Button(
            '끄기' if self.paster.is_active else '켜기',
            BUTTON_STATUS_ACTIVATED if self.paster.is_active else BUTTON_STATUS_INACTIVATED
        )
        activate_button.clicked.connect(self.toggle_activate)
        activate_button.setObjectName(f"wid-activate-{paster.id}")
        grid_layout.addWidget(activate_button, 1, 3)

        if self.paster.is_active and not is_new:
            secret_change_button.setDisabled(True)
            secret_input.setDisabled(True)
            hotkey_change_button.setDisabled(True)
            hotkey_input.setDisabled(True)

        self.setLayout(grid_layout)


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
        get_config('pasters').pop(self.paster.id)
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
            joined_hotkey = '+'.join(STORED_HOTKEY)
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
            if ((w.objectName().__contains__('sc') or w.objectName().__contains__('hk'))
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


Q_WINDOW:TOTP_Window
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
    if ['ctrl', 'alt', 'shift', 'windows'].__contains__(name):
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
                for p in get_config('pasters').values():
                    paster = Paster(p)
                    if paster.is_active:
                        paster.active_hotkey()
                        USED_HOTKEY.add(paster.hotkey)
                        USED_SECRET.add(paster.secret)
    else:
        with open(TOTP_CONFIG_FILE_NAME, mode ='w') as config:
            set_config('pasters', dict())
            set_config('auto_fill', 'False')
            set_config('paste_delay', '0.3')
            set_config('auto_enter', 'False')
            json.dump(get_config(None), config, indent = 4)


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
    global Q_WINDOW, Q_APP
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
    global Q_APP, Q_WINDOW
    keyboard.unhook_all()
    keyboard.remove_all_hotkeys()
    if Q_WINDOW and Q_WINDOW.tray_icon:
        Q_WINDOW.tray_icon.hide()
    Q_APP.quit()


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

# pyinstaller --onefile --noconsole --name "TotpPaster" --icon $env:PYTHON_MODULE_PATH\ico\totp_paster.ico --hidden-import=multiprocessing --hidden-import=keyboard $env:PYTHON_MODULE_PATH\otp.py
# pyinstaller --onefile --noconsole --name "TotpPaster" --icon .\totp_paster.ico --hidden-import=multiprocessing --hidden-import=keyboard .\pyotp_gui.py