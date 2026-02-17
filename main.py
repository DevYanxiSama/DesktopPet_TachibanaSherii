import base64
import inspect
import os
import random
import time
from typing import Callable, List
from pathlib import Path

from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QMainWindow, QApplication, QOpenGLWidget, QHBoxLayout, QVBoxLayout, QWidget, QPlainTextEdit, \
    QPushButton, QLineEdit, QSlider, QScrollArea, QComboBox, QLabel
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QObject, QEvent
import sys
import traceback as tb
import live2d.v3 as live2d
import json
from openai import OpenAI
from OpenGL.GL import *
import requests
from threading import Thread as th
import re
from abc import ABC, abstractmethod
import datetime as dt

true = True
false = False
version = "26a.2.1"  # 年，月，次数

#  开发者配置

mouth_id = "ParamMouthOpenY"


#


def log(content, level):
    today = dt.datetime.today()
    today.weekday()
    system_time = f"{today.year}年-{today.month}月-{today.day}日-{today.hour}时-{today.minute}分-{today.second}秒"
    outputContent = f"[{system_time}][{level}]: {content}"
    print(outputContent)
    filePath = Path(f"log/{today.year}年-{today.month}月-{today.day}日.log")
    if not filePath.parent.exists():
        filePath.parent.mkdir(exist_ok=true, parents=true)
    notInit = filePath.exists()
    with open(filePath, "a+", encoding='utf-8') as writer:
        if notInit:
            # 已经有内容了，附加
            writer.write(f"\n{outputContent}")
        else:
            writer.write(f"日志开始于 {today.year}年-{today.month}月-{today.day}日.log")
        writer.close()


info = lambda content: log(content, "INFO")
debug = lambda content: log(content, "DEBUG")
warn = lambda content: log(content, "WARN")
error = lambda content: log(content, "ERROR")
critical = lambda content: log(content, "CRITICAL")
console = lambda content: log(content, "CONSOLE")

debug(f"版本:{version}")


class AnimationController:

    def __init__(self):
        self.animations: List[Animation] = []
        self.registerList: List[Animation] = []
        #  添加到动画列表的缓冲
        self.finishAnimationList: List[Animation] = []

    def registerAnimation(self, animation):
        self.registerList.append(animation)

    def update(self):
        try:
            for a in self.animations:
                a.update()
                if a.isFinish():
                    self.finishAnimationList.append(a)
            else:
                if self.registerList:
                    self.animations.extend(self.registerList)
                    self.registerList = []
                if self.finishAnimationList:
                    for delAnimation in self.finishAnimationList:
                        try:
                            self.animations.remove(delAnimation)
                        except Exception as e:
                            error(f"动画更新异常:{e}\n{tb.format_exc()}")
                    else:
                        self.finishAnimationList = []
        except Exception as e:
            error(f"动画更新异常:{e}\n{tb.format_exc()}")


class Animation:

    def __init__(self, model: live2d.LAppModel, parameter: str, startValue: float, finishValue: float, playTime: float,
                 nextAnimation=None, nextWaitTime: float = 0, testFlg=False):
        self.testFlg = testFlg
        self.model = model
        self.playTime = playTime
        self.finishValue = finishValue
        self.startValue = startValue
        self.parameter = parameter
        self.createTime = time.time()
        self.playDone = False
        self.nextAnimationIsInit = False
        self.nextAnimation: Animation = nextAnimation  # 这应该是一个匿名实例
        self.nextWaitTime = nextWaitTime

    def isFinish(self):
        if not self.playDone:
            return False
        if self.playDone and not self.nextAnimation:
            return True
        if not self.nextAnimationIsInit:
            return False
        return self.nextAnimation.isFinish()

    def update(self):
        if self.nextAnimation and self.playDone and self.createTime + self.playTime + self.nextWaitTime < time.time():
            if not self.nextAnimationIsInit:
                self.nextAnimation = self.nextAnimation()
                self.nextAnimationIsInit = True
            return self.nextAnimation.update()
        if self.createTime + self.playTime < time.time():
            self.playDone = True
            self.model.SetParameterValue(self.parameter, self.finishValue)
            return
        t = (time.time() - self.createTime) / self.playTime
        nowValue = self.startValue * (1 - t) + self.finishValue * t
        self.model.SetParameterValue(self.parameter, nowValue)


class ParameterManager:

    def __init__(self):
        self.parameters: List[Parameter] = []

    def find(self, id):
        for parameter in self.parameters:
            if id == parameter:
                return parameter
        return None

    def append(self, parameter):
        self.parameters.append(parameter)


class Parameter:

    def __init__(self, live2d: live2d.LAppModel | None, type, value, id, min, max, default):
        self.type = type
        self.live2d = live2d
        self.id = id
        self.min = min
        self.max = max
        self.default = default
        self.value = self.default

    def reset(self):
        self.live2d.SetParameterValue(self.id, int(self.default))
        self.value = int(self.default)

    def ChangeValue(self, value):
        value = float(value)
        self.live2d.SetParameterValue(self.id, value)
        self.value = value

    def Animation(self, targetValue, playTime: float = 0.1, nextAnimation=None,
                  nextWaitTime: float | int = 0):
        return Animation(self.live2d, self.id, float(self.value), float(targetValue),
                         playTime, nextAnimation=nextAnimation,
                         nextWaitTime=nextWaitTime)

    def __eq__(self, other):
        if other == self.id:
            return True
        return False

    def ToDefault(self):
        self.live2d.SetParameterValue(self.id, self.default)
        self.value = self.default


class Body(ABC):  # 每个部位都需要创建一个继承Body的对象作为管理器

    def __init__(self, bodyController):
        self.bodyController: BodyController = bodyController
        self.bodyController.bodyList.append(self)
        self.map = {}  # live2d键 ->  描述
        self.nameMap = {}
        self.parameterManager: ParameterManager = ParameterManager()

    def toggle(self):
        pass

    def __eq__(self, other):
        if type(self).__name__ == other:
            return True
        return False

    def reset(self, exclude):
        for parameter in self.parameterManager.parameters:
            if parameter != exclude:
                parameter.reset()
                self.bodyController.mainWindow.config.live2dParameterData[parameter.id] = parameter.default

    def init(self):

        def functionsAction(*args):
            for function in args:
                if function:
                    function()

        def makeFunction(key):
            return lambda value=float, messageForUser=str: functionsAction(
                lambda: self.bodyController.mainWindow.animationController.registerAnimation(
                    self.parameterManager.find(key).Animation(value, 0.2)),
                self.bodyController.mainWindow.config.setLive2dParameterData(key, float(value)),
                self.toggle(),
                lambda: self.bodyController.mainWindow.ai.appendAssistantMessage(messageForUser),
                lambda: self.reset(key))

        """批量创建匿名函数"""
        for key in self.map:
            _func = makeFunction(key)
            _func.__doc__ = self.map[
                                key] + ",参数value值为0则是默认状态,1则是开始这个动作,messageForUser输出给用户的消息,不是必须携带的参数,messageForUser参数的内容必须使用以下格式:\n<think>思考内容</think><content>与用户说的话</content>,且think与content标签在一次回复中应该各仅有一对(<think>与</think>是一对,<content>与</content>是一对):"
            _func.__name__ = self.nameMap.get(key)
            self.bodyController.mainWindow.ai.functionManager.openai_function(_func)

    def setParameterValue(self, id, value):
        value = float(value)
        self.bodyController.mainWindow.config.live2dParameterData[id] = value
        self.bodyController.mainWindow.ai.live2d.SetParameterValue(id, value)


#  开始是身体

class Mouth(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)

        self.bodyController = bodyController
        self.mouthOpen = "ParamMouthOpenY"  # 控制嘴巴张开的程度,0是闭合,1是张开
        self.mouthLine = "ParamMouthForm"  # 嘴巴的曲线

        self.map = {
            self.mouthOpen: "张开嘴巴",
            self.mouthLine: "控制嘴巴曲线弧度,表达的情绪与值大小成正比",
        }

        self.nameMap = {
            self.mouthOpen: "mouth_open",
            self.mouthLine: "mouth_controller",
        }

        self.init()


class RightHand(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)

        self.bodyController = bodyController
        self.rightFistUp = "arm09R"  # 右手攥拳放到额头处
        self.rightFist = "arm008L"  # 右边的拳头
        self.rightFlat = "arm12L"  # 摊平

        self.map = {
            self.rightFistUp: "右手攥紧拳头,抬到额头高,看起来像是抬手在挡着什么",
            self.rightFist: "攥紧右手拳头,抬高到与肩膀同高, 看起来像是要打人,或者表现自己的力量",
            self.rightFlat: "右手摊平,抬高到与肩膀同高,看起来很自然,放松"
        }

        self.nameMap = {
            self.rightFistUp: "right_hand_up",
            self.rightFist: "right_fist_up",
            self.rightFlat: "right_hand_flat_and_raise_up"
        }

        self.init()

    def toggle(self):
        info("start")
        for body in self.bodyController.bodyList:
            if body == "MainBody":
                body.reset(None)


class LeftHand(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)
        self.bodyController = bodyController
        self.leftFist = "arm008R"
        self.leftThinkMode = "arm09L"  # 左边手放到下巴下像是在思考
        self.leftFlat = "arm12R"  # 摊平
        self.leftWtf = "arm07R"  # 手放到脑袋后面
        self.leftBreak = "arm10R"  # 左手放下
        self.leftFinger = "arm13R"  # 左手食指指向屏幕
        self.leftGreat = "arm14R"  # 左手竖大拇指
        self.leftHandUp = "arm16R"  # 左手张开，举起

        # 动作描述映射
        self.map = {
            self.leftFist: "攥紧左手拳头",
            self.leftThinkMode: "左手放到下巴下像是在思考",
            self.leftFlat: "左手摊平",
            self.leftWtf: "左手放到脑袋后面（无奈/疑惑）",
            self.leftBreak: "左手放下（休息）",
            self.leftFinger: "左手食指指向屏幕",
            self.leftGreat: "左手竖大拇指（点赞）",
            self.leftHandUp: "左手张开举起"
        }

        # 英文名称映射（用于函数名）
        self.nameMap = {
            self.leftFist: "left_fist",
            self.leftThinkMode: "left_think_pose",
            self.leftFlat: "left_hand_flat",
            self.leftWtf: "left_hand_behind_head",
            self.leftBreak: "left_hand_down",
            self.leftFinger: "left_hand_point",
            self.leftGreat: "left_thumb_up",
            self.leftHandUp: "left_hand_raise"
        }

        self.init()

    def toggle(self):
        info("start")
        for body in self.bodyController.bodyList:
            if body == "MainBody":
                body.reset(None)


class MainBody(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)
        self.bodyController = bodyController
        self.doubleThinkMode = "armR02"  # 左边的手放到下巴下开始思考,右手扶着左胳膊
        self.doubleIdeaMode = "arm003"  # 左边的手扶着腰，右边手食指竖起，像：”我有一计“,

        # 动作描述映射
        self.map = {
            self.doubleIdeaMode: "左边的手扶着腰，右边手食指竖起，看起来像要说:'我有一计',也可以用来表示数字1",
            self.doubleThinkMode: "左边的手撑着下巴，右手扶着左胳膊，看起来像是在思考"
        }

        # 英文名称映射（用于函数名）
        self.nameMap = {
            self.doubleIdeaMode: "body_idea_pose",
            self.doubleThinkMode: "body_think_pose"
        }

        self.init()

    def toggle(self):
        info("start")
        for body in self.bodyController.bodyList:
            if body in ["RightHand", "LeftHand"]:
                body.reset(None)


class Face(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)
        self.bodyController = bodyController
        self.facePale1 = "Pale1"  # 面部黑暗效果
        self.faceShy = "Sweet"  # 害羞
        self.faceSweat01 = "Sweat001"  # 一号汗珠
        self.faceSweat02 = "Sweat002"  # 二号汗珠

        self.map = {
            self.facePale1: "面部变暗(看起来会有些恐怖)",
            self.faceShy: "面部变红（害羞）",
            self.faceSweat01: "出现一号汗珠",
            self.faceSweat02: "出现二号汗珠"
        }

        # 英文名称映射（用于函数名）
        self.nameMap = {
            self.facePale1: "face_pale",
            self.faceShy: "face_shy",
            self.faceSweat01: "face_sweat_01",
            self.faceSweat02: "face_sweat_02"
        }
        self.init()


class Eye(Body):

    def __init__(self, bodyController):
        super().__init__(bodyController)
        self.bodyController = bodyController
        self.leftEyeOpen = "ParamEyeLOpen"  # 左边和右边眼睛睁开与闭合
        self.rightEyeOpen = "ParamEyeROpen"
        self.leftEyeSmile = "ParamEyeLSmile"  # 似乎是调整为微笑状态的眼睛
        self.rightEyeSmile = "ParamEyeRSmile"

        self.map = {
            self.leftEyeOpen: "左眼睁开程度",
            self.rightEyeOpen: "右眼睁开程度",
            self.leftEyeSmile: "左眼微笑表情",
            self.rightEyeSmile: "右眼微笑表情"
        }

        # 英文名称映射（用于函数名）
        self.nameMap = {
            self.leftEyeOpen: "left_eye_open",
            self.rightEyeOpen: "right_eye_open",
            self.leftEyeSmile: "left_eye_smile",
            self.rightEyeSmile: "right_eye_smile"
        }

        self.init()


class BodyController:

    def __init__(self, mainWindow):
        self.mainWindow: MainWindow = mainWindow
        self.bodyList: List[Body] = []

    def resetLive2dParameter(self):
        for body in self.bodyList:
            for parameter in body.parameterManager.parameters:
                parameter.reset()

    def init(self):
        info("开始加载live2d数据" + "\n" * 3)
        try:
            for key in list(self.mainWindow.config.live2dParameterData.keys()):
                info(f"读取到: {key} 值 {self.mainWindow.config.live2dParameterData[key]}")
                self.mainWindow.ai.live2d.SetParameterValue(key, float(self.mainWindow.config.live2dParameterData[key]))
            info("\n" * 3)
        except Exception as e:
            error(f"live2d数据加载失败\n{e}\n{tb.format_exc()}")

        info("开始live2d参数注册")
        _map = {}
        for body in self.bodyList:
            for key in list(body.map.keys()):
                _map[key] = body

        for count in range(self.mainWindow.ai.live2d.GetParameterCount()):
            args = {"live2d": self.mainWindow.ai.live2d}
            for name in ["id", 'type', 'value', 'max', 'min', 'default']:
                args[name] = getattr(self.mainWindow.ai.live2d.GetParameter(count), name)
            parameter = Parameter(**args)
            if parameter.id == mouth_id:
                self.mainWindow.ai.mouth = parameter

            _body = _map.get(parameter.id)
            if _body:
                _body.parameterManager.append(parameter)


class FunctionCall:

    def __init__(self):
        self.functionName = None
        self.args = None

    def update(self, test):
        return


class Function:
    def __init__(self, function: Callable):
        self.type_mapping = {
            'str': 'string',
            'int': 'integer',
            'float': 'number',
            'bool': 'boolean',
            'list': 'array',
            'dict': 'object',
            'NoneType': 'null',
            # 添加更多类型映射
            'string': 'string',
            'integer': 'integer',
            'number': 'number',
            'boolean': 'boolean',
            'array': 'array',
            'object': 'object',
            '_empty': 'string'
        }

        self.function: Callable = function
        self.doc = self.function.__doc__
        self.name = self.function.__name__

        self.parameters = {
        }
        sig = inspect.signature(self.function)
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            _type = self.type_mapping[param.annotation.__name__]
            """ 把默认值当作参数类型"""
            self.parameters[name] = {
                "type": _type,
                "description": ""
            }

        self.obj = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.doc,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters
                },
                "required": []
            }
        }


class Config:

    def __init__(self, **kwargs):
        self.savePath = Path("config/")
        self.fileName = Path("config.json")
        self.savePath.mkdir(parents=True, exist_ok=True)

        if not kwargs and (self.savePath / self.fileName).exists():
            kwargs = self.read()

        self.urls = []
        self.tokenMap = {}  # url: [token]  一个url可以有多个key,因为有的url端点会为不同分区key提供不同模型
        self.models = {}  # token -> [modelName]   同上
        self.useModel = {}  # token -> modelName
        self.useToken = {}  # url ->  token
        self.useUrl = ""
        self.appPrompt = """
        你的回复必须使用以下格式,且think与content标签在一次回复中应该各仅有一对(<think>与</think>是一对,<content>与</content>是一对):\n
        <think>思考内容</think><content>要告诉用户的内容</content>\n
        其中思考内容要包含你对事情的见解，推测，以及推测如果用户后续做出哪些举动你应该做出什么样的回复(为了辅助你后续衔接上思维)。\n系统设定:\n
        """
        self.prompt: str = ""
        self.ip = '127.0.0.1'
        self.port = 5114
        self.position = [0, 0]
        self.size = [0, 0]
        self.live2dParameterData = {}  # key -> value
        self.memory = [

        ]

        self.enabledImageModal = False
        self.windowOnTop = True
        self.streamOutPut = True
        self.autoBreath = True
        self.autoBlink = True

        if not kwargs and (self.savePath / self.fileName).exists():
            kwargs = json.loads((self.savePath / self.fileName).read_text("utf-8"))

        for key in list(kwargs.keys()):
            self.__setattr__(key, kwargs[key])
        if not self.memory:
            self.memory.append({"role": "system", "content": self.appPrompt + self.prompt})

        self.save()

    def setLive2dParameterData(self, key, value):
        self.live2dParameterData[key] = value

    def setPrompt(self, newPrompt=None):
        if newPrompt is not None:
            self.prompt = newPrompt
        self.memory = []
        self.memory.append({"role": "system", "content": self.appPrompt + self.prompt})

    def save(self):
        try:
            (self.savePath / self.fileName).write_text(json.dumps(self.export(), ensure_ascii=False, indent=4),
                                                       encoding="utf-8")
        except Exception as e:
            error(f"保存配置文件出错\n{e}\n{tb.format_exc()}\n{self.export()}")

    def export(self):
        return {"urls": self.urls,
                "tokenMap": self.tokenMap,
                "useModel": self.useModel,
                "windowOnTop": self.windowOnTop,
                "autoBreath": self.autoBreath,
                "autoBlink": self.autoBlink,
                "models": self.models,
                "prompt": self.prompt,
                "memory": self.memory,
                "ip": self.ip,
                "port": self.port,
                "streamOutPut": self.streamOutPut,
                "useUrl": self.useUrl,
                "useToken": self.useToken,
                "position": self.position,
                "live2dParameterData": self.live2dParameterData,
                "size": self.size,
                "enabledImageModal": self.enabledImageModal,

                }

    def read(self) -> dict:
        if (self.savePath / self.fileName).exists():
            return json.loads((self.savePath / self.fileName).read_text("utf-8"))


class FunctionManager:

    def __init__(self):
        self.functions: List[Function] = []

    def tools(self):
        return [i.obj for i in self.functions]

    def add(self, function: Function):
        self.functions.append(function)

    def openai_function(self, func=None):
        if func:
            _function = Function(func)
            self.functions.append(_function)
            return _function

        def __function(func: Callable):
            _function = Function(func)
            self.functions.append(_function)
            return _function

        return __function

    def get(self, functionName) -> Function | None:
        for _function in self.functions:
            if _function.name == functionName:
                return _function
        else:
            return None


class MainWindow(QMainWindow):

    def autoSaveConfigMethod(self):
        self.config.save()

    def __init__(self):
        super().__init__()

        self.startPosition = (0, 0)
        self.offset = (0, 0)
        self.dragging = True
        self.windowSize = [700, 200]  # 123123
        self.is_topmost = False

        self.animationController: AnimationController = AnimationController()

        self.autoSaveConfig = QTimer()
        self.autoSaveConfig.timeout.connect(self.autoSaveConfigMethod)
        self.function = None
        self.aiName = "橘雪莉"
        self.resize(*self.windowSize)
        self.setWindowTitle("通用框架可行性测试")
        self.ai: AI | None = None
        self.config: Config | None = None
        self.settingWindow: SettingWindow | None = None
        self.isClose = False
        self.bodyController: BodyController = BodyController(self)
        self.enabledImageModal = False  # 是否启用视觉模态

        self.thinkThread: th | None = None

        #  设置窗口属性
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        #  下面是涉及布局的了
        self.centralWidget = QWidget()
        self.centralLayout = QVBoxLayout(self.centralWidget)
        self.setCentralWidget(self.centralWidget)

        self.centralWidget.setStyleSheet("""
        *{
        border-radius: 15px;
        background-color:rgba(200,200,200,0.5);
        padding:0;
        margin:0;
        }

        QPushButton:hover{
        background-color:rgba(150,150,150,0.5);
        }

        QPlainTextEdit:hover{
        background-color:rgba(150,150,150,0.5);
        }

        QPlainTextEdit{
        padding:5px;
        }

        """)

        self.titleWidget = QWidget(self.centralWidget)
        self.centralLayout.addWidget(self.titleWidget)
        self.titleLayout = QHBoxLayout(self.titleWidget)

        self.titleWidget.setMaximumHeight(50)
        self.title = QLineEdit()
        self.title.setStyleSheet("""
        background-color:rgba(0,0,0,0);
        """)
        self.title.setReadOnly(True)
        self.title.setText(self.windowTitle())
        self.titleLayout.addWidget(self.title)

        self.titleLayout.addStretch()

        self.toggleDisplayMode = QPushButton("切换")
        self.toggleDisplayMode.setMinimumSize(80, 30)
        self.toggleDisplayMode.clicked.connect(self.toggleDisplayModeMethod)

        self.toggleTopmostButton = QPushButton("T")
        self.toggleTopmostButton.setMinimumSize(50, 30)
        self.toggleTopmostButton.clicked.connect(self.toggle_topmost)
        self.toggleTopmostButton.setStyleSheet("""
        QPushButton{
            border-radius:5px;
            background-color:rgba(50,50,230,0.9);
        }
        QPushButton:hover{
            background-color:rgba(50,50,200,0.8);
        }
        """)

        self.exitButton = QPushButton("退出")
        self.exitButton.setMinimumSize(50, 30)
        self.exitButton.clicked.connect(sys.exit)
        self.exitButton.setStyleSheet("""
        QPushButton{
            border-radius:5px;
            background-color:rgba(230,0,0,0.9);
        }
        QPushButton:hover{
            background-color:rgba(200,0,0,0.8);
        }
        """)
        [self.titleLayout.addWidget(i) for i in [self.toggleDisplayMode, self.toggleTopmostButton, self.exitButton]]

        self.mainWidget = QWidget(self.centralWidget)
        self.centralLayout.addWidget(self.mainWidget)
        self.mainLayout = QHBoxLayout(self.mainWidget)

        self.openglWidget = OpenGlWidget(self)
        self.openglWidget.setCursor(Qt.OpenHandCursor)

        self.contentWidget = QWidget()  # 要往这里面添加功能性组件
        self.contentLayout = QVBoxLayout(self.contentWidget)

        self.mainLayout.addWidget(self.openglWidget)
        self.mainLayout.addWidget(self.contentWidget)

        self.AIMessage = QPlainTextEdit()
        self.AIMessage.setReadOnly(True)
        self.AIMessage.viewport().setCursor(Qt.ArrowCursor)
        self.AIMessage.setPlaceholderText(f"与 {self.aiName} 聊些什么")
        self.contentLayout.addWidget(self.AIMessage)
        self.contentLayout.addStretch()

        self.userMessage = PlainTextEdit(self)
        self.sendMessage = QPushButton()
        self.sendMessage.setText("发送")
        self.setting = QPushButton()
        self.setting.setText("设置")

        self.sendMessage.clicked.connect(self.chat)

        self.sendMessage.setMinimumHeight(30)
        self.setting.setMinimumHeight(30)

        [self.contentLayout.addWidget(i) for i in [self.userMessage, self.sendMessage, self.setting]]

    def toggleDisplayModeMethod(self):
        info(self.isClose)
        if not self.isClose:
            self.contentWidget.close()
            self.isClose = True
        else:
            self.contentWidget.show()
            self.isClose = False

    def mousePressEvent(self, a0):
        self.dragging = a0.button() == Qt.LeftButton
        if self.dragging:
            self.startPosition = a0.globalPos() - self.pos()

    def mouseMoveEvent(self, a0):
        if self.dragging:
            newPos = a0.globalPos() - self.startPosition
            self.move(newPos)

    def init(self):
        if self.config.windowOnTop:
            self.toggle_topmost()

        self.ai = AI(self)
        self.setAIMessage(self.ai.getLastAIMessage())
        self.resize(*self.config.size)
        self.move(*self.config.position)
        self.enabledImageModal = self.config.enabledImageModal
        self.autoSaveConfig.start(20000)

    def setAIMessage(self, text):
        self.AIMessage.setPlainText(text)

    def toggle_topmost(self):
        flags = self.windowFlags()

        if self.is_topmost:
            flags &= ~Qt.WindowStaysOnTopHint
            self.is_topmost = False
        else:
            flags |= Qt.WindowStaysOnTopHint
            self.is_topmost = True
        self.config.windowOnTop = self.is_topmost
        self.config.save()
        self.setWindowFlags(flags)
        self.show()

    def resizeEvent(self, a0):
        size = a0.size()
        self.config.size = [size.width(), size.height()]
        self.config.save()

    def chat(self):
        if not self.thinkThread or self.thinkThread.ident is not None and not self.thinkThread.is_alive():
            if not bool(self.userMessage.toPlainText()):
                return

            #  这里还要添加  图片处理
            images = []
            if self.enabledImageModal and self.userMessage.images:
                for image in self.userMessage.images:
                    images.append(f"data:image/jpeg;base64,{base64.b64encode(Path(image).read_bytes()).decode("utf-8")}")

            self.ai.addUserMessage(self.userMessage.toPlainText(), images)
            self.userMessage.setPlaceholderText(self.userMessage.toPlainText())
            self.AIMessage.setPlainText(f"{self.aiName} 思考中...")
            self.userMessage.clear()
            self.thinkThread = th(target=self.ai.chat)
            self.thinkThread.daemon = True
            self.thinkThread.start()


class FileWidget(QWidget):

    def __init__(self, _parent, filePath):
        super().__init__()
        self._parent: MainWindow = _parent
        self.mainLayout = QVBoxLayout()
        self.setLayout(self.mainLayout)
        self.mainLayout.addStretch()

        self.filePath = filePath
        # background-image: url({self.filePath});
        size = 100
        self.setStyleSheet(f"""
        border-radius:5px;
        border-image: url({filePath}) 0 0 0 0 stretch stretch;
        min-width:{size}px;
        min-height:{size * 0.8}px;
        max-width:{size}px;
        max-height:{size * 0.8}px;
        """)

        childWidget = QWidget()
        childLayout = QHBoxLayout(childWidget)

        _del = QPushButton("x")
        _button_size = 25
        _del.setStyleSheet(f"""
        border-image:none;
        min-width:{_button_size}px;
        min-height:{_button_size}px;
        max-width:{_button_size}px;
        max-height:{_button_size}px;
        padding:5px;
        """)
        _del.clicked.connect(self.delSelf)

        childLayout.addStretch()
        childLayout.addWidget(_del)
        self.mainLayout.addWidget(childWidget)

    def __str__(self):
        return self.filePath

    def mousePressEvent(self, a0):
        if a0.button() == Qt.LeftButton:
            os.system(f"start {self.filePath}")

    def delSelf(self):
        try:
            self.close()
            self._parent.userMessage.images.remove(self.filePath)
            info("对象销毁成功")
        except Exception as e:
            error(f"销毁文件对象失败:\n{e}\n{tb.format_exc()}")


class PlainTextEdit(QPlainTextEdit):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__(parent)

        self.images = []  # 路径
        self.imageObjs: list[FileWidget] = []
        self._parent: MainWindow = parent
        self._layout = QHBoxLayout()
        self.viewport().setLayout(self._layout)
        self._layout.addStretch()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() != Qt.ShiftModifier:
            self._parent.chat()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if not self._parent.enabledImageModal:
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            # 改变样式表示可以放置
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        """拖拽离开事件"""
        pass

    def dropEvent(self, event: QDropEvent):
        """放置事件"""

        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                # 获取第一个文件的绝对路径
                file_path = urls[0].toLocalFile()

                _imageWidget = FileWidget(self._parent, file_path)
                self._layout.addWidget(_imageWidget)
                self.images.append(str(_imageWidget))
                self.imageObjs.append(_imageWidget)
                # 发射信号  虽然暂时没用
                self.file_dropped.emit(file_path)

                event.acceptProposedAction()
        else:
            event.ignore()

    def clear(self):
        self.images.clear()
        for obj in self.imageObjs:
            obj.close()
        super().clear()


class AI:

    def __init__(self, parent: MainWindow):
        self.parent = parent
        self.config: Config = self.parent.config
        self.aiName = "橘雪莉"
        self.live2d: live2d.LAppModel | None = None
        self.functionManager: FunctionManager = FunctionManager()

        self.lastedChat = time.time()
        self.mouth: Parameter = None

        self.ai: OpenAI | None = None
        if self.config.useUrl and self.config.useToken.get(self.config.useUrl):
            self.ai = OpenAI(base_url=self.config.useUrl, api_key=self.config.useToken.get(self.config.useUrl))
        self.init()

    def connect(self, url, key) -> OpenAI | None:
        if not key or not url:
            return
        self.ai = OpenAI(base_url=url, api_key=key)
        return self.ai

    def addUserMessage(self, text, images: list[str] | None | list[bytes] = None):
        """image 需要自行转base64然后传入"""
        content = []
        if text:
            content.append({"type": "text", "text": text})
        for image in images:
            content.append({"type": "image_url", "image_url": {"url": image}})
        self.config.memory.append({"role": "user", "content": content})

    def appendAssistantMessage(self, message, toolCall=None):
        addMemory = {"role": "assistant", "content": message}
        if toolCall:
            addMemory['tool_calls'] = toolCall
        if not message or message == str:
            return
        self.config.memory.append(addMemory)

        lastMessage = self.getLastAIMessage()
        history = ""
        if lastMessage:
            for i in lastMessage:
                if not self.parent.function:
                    if self.mouth:
                        _timeMap = [i / 10 for i in range(1, 5)]
                        _timeMap.append(0)
                        for _i in range(0, 4):
                            random.shuffle(_timeMap)

                        self.mouth.ChangeValue(_timeMap[0])
                    self.parent.function = lambda: self.parent.setAIMessage(history + i)
                history += i
                time.sleep(0.1)

        self.mouth.ChangeValue(0)

    def getLastAIMessage(self):
        aiMessage = ""
        for i in self.config.memory:
            if i['role'] == "assistant":
                try:
                    result = re.search("<content>(.*?)</content>", i["content"], re.DOTALL)
                    aiMessage = result.group(1) if result is not None else ""
                except Exception as e:
                    error(f"获取最后一次ai消息发生错误:\n{e}\n{tb.format_exc()}")
        return aiMessage

    def chat(self):
        try:
            if not self.config.memory:
                self.config.setPrompt()
            response = self.ai.chat.completions.create(
                model=self.config.useModel.get(self.config.useToken.get(self.config.useUrl)),
                messages=self.config.memory,
                stream=self.config.streamOutPut,
                tools=self.functionManager.tools(),
                tool_choice="auto"
            )
            self.lastedChat = time.time()
            Path("file.json").write_text(response.model_dump_json(indent=4), encoding="utf-8")
            if not response.choices[0].message.content:
                response.choices[0].message.content = ""
            self.appendAssistantMessage(response.choices[0].message.content)
            try:
                if response.choices[0].message.tool_calls:
                    for tool in response.choices[0].message.tool_calls:
                        function = self.functionManager.get(tool.function.name)
                        if function: function.function(**json.loads(tool.function.arguments))
            except Exception as e:
                error(f"工具调用错误:\n{e}\n{tb.format_exc()}")
            self.config.save()
        except Exception as e:
            print(f"{e}\n{tb.format_exc()}")

    def init(self):
        pass


class SettingWindow(QMainWindow):

    def __init__(self, parent: MainWindow):
        super().__init__(parent)
        self._parent: MainWindow = parent
        self.setWindowTitle("设置")
        self.resize(1000, 650)
        self.changeLock = False
        self.config: Config = Config()

        #  self.setAttribute(Qt.WA_TranslucentBackground)

        self.centralWidget = QWidget()
        self.centralLayout = QVBoxLayout(self.centralWidget)
        self.setCentralWidget(self.centralWidget)

        self.contentWidget = QWidget()
        self.contentLayout = QHBoxLayout(self.contentWidget)

        self.scrollObj = QScrollArea()  # 这里用来显示设置列表，点击然后在settingContent里调
        self.scrollObj.setWidgetResizable(True)
        self.scrollWidget = QWidget()
        self.scrollObj.setWidget(self.scrollWidget)
        self.scrollLayout = QVBoxLayout(self.scrollWidget)
        self.scrollObj.setMaximumWidth(int(self.width() * 0.35))
        self.scrollObj.setMinimumWidth(int(self.width() * 0.3))

        """这里添加设置内容"""

        self.setStyleSheet("""
        QPushButton{
        border:none;
        border-bottom: 2px solid black;
        min-height:30px;
        background-color:rgba(240,240,240,0.9);
        }
        
        QPushButton:hover{
        background-color:rgba(200,200,200,0.8);
        }
        """)

        llmSetting = QPushButton("大模型设置")
        llmSetting.clicked.connect(self.llmSetting)

        live2dSetting = QPushButton("Live2d设置")
        live2dSetting.clicked.connect(self.live2dSetting)

        memoryAndPrompt = QPushButton("记忆与提示词")

        windowSetting = QPushButton("窗口设置")
        windowSetting.clicked.connect(self.windowSetting)

        saveConfig = QPushButton("保存配置")
        exportConfig = QPushButton("导出配置(暂时没用)")
        other = QPushButton("关于软件")

        memoryAndPrompt.clicked.connect(self.memoryAndPrompt)
        saveConfig.clicked.connect(self.config.save)
        exportConfig.clicked.connect(self.config.export)
        other.clicked.connect(self.other)

        [self.scrollLayout.addWidget(i) for i in
         [llmSetting, live2dSetting, windowSetting, memoryAndPrompt, saveConfig, exportConfig, other]]
        self.scrollLayout.addStretch()

        """"""

        self.contentLayout.addWidget(self.scrollObj)

        self.settingContentWidget = QWidget()
        self.settingContentLayout = QVBoxLayout(self.settingContentWidget)
        self.settingContentWidget.setStyleSheet("""
        QLineEdit{
        border:none;
        background-color:rgba(0,0,0,0);
        }
        """)
        self.settingContentWidget.setMinimumWidth(int(self.width() * 0.6))

        self.chooseTip = QLineEdit("请在左侧列表中选择一项")
        self.chooseTip.setReadOnly(True)
        self.chooseTip.setAlignment(Qt.AlignCenter)
        self.chooseTip.setStyleSheet("""
        background-color:rgba(0,0,0,0);
        border:none;
        """)
        self.settingContentLayout.addStretch()
        self.settingContentLayout.addWidget(self.chooseTip)
        self.settingContentLayout.addStretch()

        self.contentLayout.addWidget(self.settingContentWidget)

        self.centralLayout.addWidget(self.contentWidget)

    def clearSettingContent(self, layout=None):
        if not layout:
            layout = self.settingContentLayout
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()  # 安全删除控件

    @staticmethod
    def getModelList(url, apiKey="0") -> List[str]:
        """仅获取模型id"""
        headers = {
            "Authorization": f"Bearer {apiKey}",  # OpenAI标准格式
            "Content-Type": "application/json"
        }
        response = requests.get(url + "/models", headers=headers)
        if response.ok:
            return [i['id'] for i in response.json()['data']]
        return []

    def live2dSetting(self):
        """切换为live2d设置界面"""
        self.clearSettingContent()

        _lineEdit_tip = QLineEdit("暂时没有东西")

        self.settingContentLayout.addWidget(_lineEdit_tip)

    def windowSetting(self):
        self.clearSettingContent()
        mainWidget = QWidget()
        mainLayout = QVBoxLayout(mainWidget)

        _lineEdit_title = QLineEdit("窗口大小")
        _lineEdit_title.setReadOnly(True)

        _childWidget = QWidget()
        _childLayout = QHBoxLayout(_childWidget)
        _size0 = QPushButton("小")  # 123123
        _size1 = QPushButton("中")
        _size2 = QPushButton("大")
        _size0.clicked.connect(lambda: self._parent.resize(700, 400))
        _size1.clicked.connect(lambda: self._parent.resize(800, 400))
        _size2.clicked.connect(lambda: self._parent.resize(1000, 400))

        [_childLayout.addWidget(i) for i in [_size0, _size1, _size2]]
        [mainLayout.addWidget(i) for i in [_lineEdit_title, _childWidget]]
        mainLayout.addStretch()
        self.settingContentLayout.addWidget(mainWidget)

    def other(self):
        self.clearSettingContent()
        mainWidget = QWidget()
        mainLayout = QVBoxLayout(mainWidget)

        _version = QLineEdit(f"版本号:{version}")
        _developer = QLineEdit(f"开发者:DevYanxiSama")
        _github = QPushButton("项目github")
        _github.clicked.connect(lambda: os.system(f"start https://github.com/DevYanxiSama/DesktopPet_TachibanaSherii"))
        _other = QPlainTextEdit("- 如有问题请提交issue,清晰描述问题发生前做了什么。\n"
                                "- 本软件完全免费,如果你以任何渠道购买到本软件,那么你被骗。\n"
                                "- 开发者愿意持续更新并改善体验,欢迎大家点击下方按钮加入群聊讨论。")

        _childTitle = QLineEdit("联系方式")
        [i.setReadOnly(True) for i in [_version, _developer, _other, _childTitle]]

        _childWidget = QWidget()
        _childLayout = QHBoxLayout(_childWidget)

        _developerQQ = QPushButton("开发者QQ")
        _chatGroup = QPushButton("交流群")
        _developerQQ.clicked.connect(
            lambda: os.system("start http://wpa.qq.com/msgrd?v=3&uin=1810153793&site=qq&menu=yes"))
        _chatGroup.clicked.connect(lambda: os.system(
            "start https://qm.qq.com/cgi-bin/qm/qr?k=kZjF2gFT3TuYYv8pcG9UrJLFo1CkGB96&jump_from=webapi&authKey=SCOMiZXuWJTwi2xG+GW8ve9X0/XzTYZBiq8xKEbBCj82B1sHhzieJbcDAehioVRo"))
        [_childLayout.addWidget(i) for i in [_developerQQ, _chatGroup]]

        _list = [_version, _developer, _github, _other, _childTitle, _childWidget]
        [mainLayout.addWidget(i) for i in _list]

        self.settingContentLayout.addWidget(mainWidget)

    def memoryAndPrompt(self):
        self.clearSettingContent()

        parentWidget = QWidget()

        scrollObj = QScrollArea()
        scrollObj.setWidget(parentWidget)
        scrollObj.setWidgetResizable(True)
        scrollLayout = QVBoxLayout(scrollObj)
        childWidget = QWidget()
        childLayout = QVBoxLayout(childWidget)

        newPrompt = QPlainTextEdit()
        if self.config.prompt:
            newPrompt.setPlainText(self.config.prompt)
        newPrompt.setPlaceholderText("这里输入大模型的提示词.")
        savePrompt = QPushButton("保存提示词(清除记忆后生效)")
        clearMemory = QPushButton("清除记忆")
        resetPrompt = QPushButton("不保存提示词")

        savePrompt.clicked.connect(lambda: self.savePrompt(newPrompt))
        clearMemory.clicked.connect(self.clearMemory)
        resetPrompt.clicked.connect(lambda: self.resetPrompt(newPrompt))
        [childLayout.addWidget(i) for i in [newPrompt, savePrompt, resetPrompt, clearMemory]]
        _childWidget = QWidget()
        _childLayout = QVBoxLayout(_childWidget)
        fileDropWidget = FileDropWidget(_childWidget)
        fileDropWidget.file_dropped.connect(lambda filePath: self.loadPrompt(filePath, newPrompt))

        exportPrompt = QPushButton("导出提示词")
        exportPrompt.clicked.connect(self.exportPrompt)

        [_childLayout.addWidget(i) for i in [fileDropWidget, exportPrompt]]

        [scrollLayout.addWidget(i) for i in [childWidget, _childWidget]]
        self.settingContentLayout.addWidget(scrollObj)

    def resetPrompt(self, promptEdit: QPlainTextEdit):
        promptEdit.setPlainText(self.config.prompt)

    def exportPrompt(self):
        outPutPath = Path("output/")
        outPutPath.mkdir(exist_ok=True, parents=True)
        filePath = (outPutPath / Path(f"prompt{random.randint(1000, 9999)}.txt"))
        filePath.write_text(self.config.prompt, encoding="utf-8")
        os.startfile(outPutPath)

    def loadPrompt(self, filePath, promptEdit: QPlainTextEdit):
        promptEdit.setPlainText(Path(filePath).read_text("utf-8"))

    def savePrompt(self, newPrompt: QPlainTextEdit):
        try:
            self.config.setPrompt(newPrompt.toPlainText())
            self.config.save()
        except Exception as e:
            error(f"保存提示词异常:\n{e}\n{tb.format_exc()}")

    def clearMemory(self):
        try:
            self.config.memory = []
            self._parent.AIMessage.clear()
            self.config.setPrompt()
            self.config.save()
            self.config.live2dParameterData = {}
            self._parent.bodyController.resetLive2dParameter()
        except Exception as e:
            error(f"清除记忆异常:\n{e}\n{tb.format_exc()}")

    def llmSetting(self):
        """切换为大模型设置界面"""
        self.clearSettingContent()
        toggleModelWidget = QWidget()
        toggleModelLayout = QVBoxLayout(toggleModelWidget)

        _lineEdit_toggleAPI = QLineEdit("选择可用的URL端点")
        _lineEdit_toggleAPI.setReadOnly(True)

        _lineEdit_toggleTokenAndModel = QLineEdit("选择可用的token与token下可用的模型")
        _lineEdit_toggleTokenAndModel.setReadOnly(True)

        urlComboBox = QComboBox()

        childWidget = QWidget()
        childLayout = QHBoxLayout(childWidget)

        tokenComboBox = QComboBox()
        modelComboBox = QComboBox()

        [childLayout.addWidget(i) for i in [tokenComboBox, modelComboBox]]

        self.loadUrls(urlComboBox)
        self.loadTokens(tokenComboBox)
        self.loadModels(modelComboBox)
        urlComboBox.currentTextChanged.connect(
            lambda text: self.onUrlComboChanged(text, tokenComboBox, modelComboBox))

        tokenComboBox.currentTextChanged.connect(lambda text: self.onTokenComboChanged(text, modelComboBox))
        modelComboBox.currentTextChanged.connect(lambda text: self.onModelComboChanged(text))

        _childWidget = QWidget()  # 在childWidget下面
        _childLayout = QHBoxLayout(_childWidget)

        delUrl = QPushButton("删除当前选中的URL端点")
        delToken = QPushButton("删除当前选中的Token")

        delUrl.clicked.connect(lambda: self.onDelUrl(urlComboBox, tokenComboBox, modelComboBox))
        delToken.clicked.connect(lambda: self.onDelToken(tokenComboBox, modelComboBox))

        __childWidget = QWidget()
        __childLayout = QVBoxLayout(__childWidget)

        newUrl = QLineEdit()
        newUrl.setPlaceholderText("输入新的URL端点")
        newUrl.setAlignment(Qt.AlignLeft)
        newToken = QLineEdit()
        newToken.setAlignment(Qt.AlignLeft)
        newToken.setPlaceholderText("为当前选中的端点添加token")

        saveUrl = QPushButton("保存URL端点")
        saveToken = QPushButton("把token保存到选中的URL")
        getModel = QPushButton("从选中的端点中获取模型列表")

        saveUrl.clicked.connect(lambda: self.addUrl(newUrl, urlComboBox))
        saveToken.clicked.connect(lambda: self.addToken(newToken, tokenComboBox))
        getModel.clicked.connect(lambda: th(target=lambda: self.addModels(modelComboBox)).start())

        [_childLayout.addWidget(i) for i in [delUrl, delToken]]
        [__childLayout.addWidget(i) for i in [newUrl, saveUrl, newToken, saveToken, getModel]]
        __childLayout.addStretch()

        imageModalTitle = QPlainTextEdit(
            f"如果为不支持视觉模态的大模型启用,可能会导致崩溃.\n视觉模态: {self._parent.enabledImageModal}\nTrue为启用\nFalse为禁用")
        imageModalTitle.setReadOnly(True)
        enabledImageModal = QPushButton("启用/禁用 视觉模态")
        enabledImageModal.clicked.connect(lambda:self.toggleImageModal(imageModalTitle))
        [__childLayout.addWidget(i) for i in [imageModalTitle, enabledImageModal]]

        [toggleModelLayout.addWidget(i) for i in
         [_lineEdit_toggleAPI, urlComboBox, _lineEdit_toggleTokenAndModel, childWidget, _childWidget, __childWidget]]
        toggleModelLayout.addStretch()

        self.settingContentLayout.addWidget(toggleModelWidget)

    def toggleImageModal(self, imageModalTitle: QPlainTextEdit):
        self._parent.enabledImageModal = not self._parent.enabledImageModal
        self.config.enabledImageModal = self._parent.enabledImageModal
        imageModalTitle.setPlainText(f"如果为不支持视觉模态的大模型启用,可能会导致崩溃.\n视觉模态: {self._parent.enabledImageModal}\nTrue为启用\nFalse为禁用")

    def addModels(self, modelComboBox):
        if not self.config.useUrl or not self.config.useToken: return
        self.changeLock = True
        models = self.getModelList(self.config.useUrl, self.config.useToken[self.config.useUrl])
        self.config.models[self.config.useToken[self.config.useUrl]] = models
        self.loadModels(modelComboBox)
        self.config.save()
        QTimer.singleShot(100, self.unlock)

    def unlock(self):
        self.changeLock = False

    def addToken(self, textEdit: QLineEdit, tokenComboBox):
        if not self.config.useUrl: return
        self.changeLock = True
        text = textEdit.text()
        if not text: return
        textEdit.clear()
        self.config.tokenMap[self.config.useUrl].append(text)
        self.loadTokens(tokenComboBox)
        self.config.save()
        self.changeLock = False

    def addUrl(self, textEdit: QLineEdit, urlComboBox):
        self.changeLock = True
        text = textEdit.text()
        if not text: return
        textEdit.clear()
        self.config.urls.append(text)
        self.config.tokenMap[text] = []
        self.loadUrls(urlComboBox)
        self.config.save()
        self.changeLock = False

    def onDelToken(self, tokenComboBox, modelComboBox):
        if not self.config.useToken.get(self.config.useUrl): return
        self.changeLock = True
        """
        第一是删除tokenMap里的
        第二是删除token对应的models
        """
        if self.config.models.get(self.config.useToken.get(self.config.useUrl)):
            self.config.models.pop(self.config.useToken.get(self.config.useUrl))
        try:
            if self.config.tokenMap.get(self.config.useUrl):
                self.config.tokenMap[self.config.useUrl].remove(self.config.useToken.get(self.config.useUrl))
            self.config.useToken.pop(self.config.useUrl)
        except Exception as e:
            print(f"删除失败:{e}\n{tb.format_exc()}")
        self.loadTokens(tokenComboBox)
        self.loadModels(modelComboBox)
        self.config.save()
        self.changeLock = True

    def onDelUrl(self, urlComboBox, tokenComboBox, modelComboBox):
        if self.config.useUrl not in self.config.urls: return
        self.changeLock = True
        self.config.urls.remove(self.config.useUrl)
        tokens = self.config.tokenMap.get(self.config.useUrl)
        if tokens:
            for token in tokens:
                if token in list(self.config.models.keys()):
                    self.config.models.pop(token)
        if self.config.useUrl in list(self.config.tokenMap.keys()):
            self.config.tokenMap.pop(self.config.useUrl)
        if self.config.useToken.get(self.config.useUrl):
            self.config.useToken.pop(self.config.useUrl)
        self.config.useUrl = None
        self.loadUrls(urlComboBox)
        self.loadTokens(tokenComboBox)
        self.loadModels(modelComboBox)
        self.config.save()
        self.changeLock = False

    def onUrlComboChanged(self, text, tokenComboBox, modelComboBox):
        if self.changeLock: return
        self.changeLock = True
        self.config.useUrl = text
        self.loadTokens(tokenComboBox)
        self.loadModels(modelComboBox)
        self._parent.ai.connect(self.config.useUrl, self.config.useToken.get(self.config.useUrl))
        self.config.save()
        QTimer.singleShot(100, self.unlock)

    def onTokenComboChanged(self, text, modelComboBox):
        if self.changeLock: return
        self.changeLock = True
        self.config.useToken[self.config.useUrl] = text
        self.loadModels(modelComboBox)
        self._parent.ai.connect(self.config.useUrl, self.config.useToken.get(self.config.useUrl))
        self.config.save()
        QTimer.singleShot(100, self.unlock)

    def onModelComboChanged(self, text):
        if self.changeLock: return
        self.changeLock = True
        self.config.useModel[self.config.useToken[self.config.useUrl]] = text
        self.config.save()
        QTimer.singleShot(100, self.unlock)

    def loadUrls(self, urlComboBox: QComboBox):
        urlComboBox.clear()
        urlComboBox.addItems(self.config.urls)
        if self.config.useUrl:
            urlComboBox.setCurrentText(self.config.useUrl)
        elif self.config.urls:
            self.config.useUrl = self.config.urls[0]
            urlComboBox.setCurrentText(self.config.useUrl)

    def loadModels(self, modelComboBox: QComboBox):
        modelComboBox.clear()
        token = self.config.useToken.get(self.config.useUrl)
        if not token:
            return
        models = self.config.models.get(token)
        if models:
            modelComboBox.addItems(models)
            useModel = self.config.useModel.get(token)
            if not useModel: useModel, self.config.useModel[token] = models[0], models[0]
            modelComboBox.setCurrentText(useModel)

    def loadTokens(self, tokenComboBox):
        tokenComboBox.clear()
        useUrl = self.config.useUrl
        if not useUrl:
            return
        tokens = self.config.tokenMap.get(useUrl)
        if tokens:
            tokenComboBox.addItems(tokens)
            useToken = self.config.useToken.get(useUrl)
            if not useToken: useToken, self.config.useToken[useUrl] = tokens[0], tokens[0]
            tokenComboBox.setCurrentText(useToken)

    def resizeEvent(self, a0):
        self.scrollObj.setMaximumWidth(int(self.width() * 0.4))
        self.settingContentWidget.setMinimumWidth(int(self.width() * 0.6))


class OpenGlWidget(QOpenGLWidget):

    def __init__(self, parent):
        super().__init__(parent)
        self._parent: MainWindow = parent
        self.live2d: live2d.LAppModel | None = None
        self.backgroundColor = [0, 0, 0, 0]
        self.timer: QTimer = QTimer()
        self.isInit = False
        self.timer.timeout.connect(self.__update)
        self.setMinimumWidth(450)
        self.setMinimumHeight(570)

        self.setAttribute(Qt.WA_TranslucentBackground)

    def loadModel(self, filePath):
        self.live2d = live2d.LAppModel()
        self._parent.ai.live2d = self.live2d
        self.live2d.LoadModelJson(filePath)
        self.live2dResize()
        self.timer.start(16)

    def paintGL(self):
        glClearColor(*self.backgroundColor)
        glClear(GL_COLOR_BUFFER_BIT)
        if self.live2d:
            self.live2d.Draw()

    def __update(self):
        self.live2d.Update()
        self.update()
        self._parent.animationController.update()
        #  加一个检查parent function
        if self._parent.function:
            self._parent.function()
            self._parent.function = None
        self._parent.config.position = (self._parent.x(), self._parent.y())

        if not self.isInit and self._parent.ai.live2d is not None:
            #  同时也负责检查组件初始化吧
            info("初始化live2d参数")
            body = [RightHand(self._parent.bodyController), MainBody(self._parent.bodyController),
                    LeftHand(self._parent.bodyController), Face(self._parent.bodyController),
                    Eye(self._parent.bodyController), Mouth(self._parent.bodyController)]
            #  在初始化它之前，必须先创建所有live2d身体部位实例
            try:
                self._parent.bodyController.init()
            except Exception as e:
                error(f"{e}\b{tb.format_exc()}")
            info("身体控制器初始化完毕")

            [_body.init() for _body in body]
            info("初始化完毕")
            self.isInit = True

    def live2dResize(self):
        if self.live2d:
            self.live2d.Resize(self.width(), self.height())

    def initializeGL(self):
        glEnable(GL_BLEND)
        glClearColor(*self.backgroundColor)

        if live2d.LIVE2D_VERSION == 3:
            live2d.glInit()
        QTimer.singleShot(100, lambda: self.loadModel("models/Sherry-ModelMandou/Sherry - Model.model3.json"))

    def resizeEvent(self, e):
        self.live2dResize()


class FileDropWidget(QWidget):
    """自定义文件拖放部件"""
    # 定义信号，当文件被拖入时发射
    file_dropped = pyqtSignal(str)  # 发射文件路径

    def __init__(self, parent=None):
        super().__init__(parent)

        # 设置接受拖放
        self.setAcceptDrops(True)

        # 设置最小尺寸
        self.setMinimumSize(200, 150)
        self.setStyleSheet("""
        border: 2px dashed #aaa;""")
        # 创建布局和标签
        layout = QVBoxLayout(self)
        self.label = QLabel("拖放提示词文件到此处\n(xxx.txt)")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 20px;")
        layout.addWidget(self.label)

        # 文件路径标签
        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("font-size: 20px; padding-top: 10px;")
        layout.addWidget(self.file_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            # 改变样式表示可以放置
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        """拖拽离开事件"""
        pass

    def dropEvent(self, event: QDropEvent):
        """放置事件"""

        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                # 获取第一个文件的绝对路径
                file_path = urls[0].toLocalFile()

                # 显示文件名
                self.label.setText("已选择文件:")
                self.file_label.setText(file_path)

                # 发射信号
                self.file_dropped.emit(file_path)

                event.acceptProposedAction()
        else:
            event.ignore()

    def clear(self):
        """清空显示"""
        self.label.setText("拖放文件到此处")
        self.file_label.setText("")


if __name__ == '__main__':
    try:
        live2d.init()
        app = QApplication(sys.argv)
        window = MainWindow()
        settingWindow = SettingWindow(window)
        window.config = settingWindow.config
        window.settingWindow = settingWindow
        window.init()
        window.show()
        window.setting.clicked.connect(settingWindow.show)
        sys.exit(app.exec_())
    except Exception as e:
        print(f"{e}\n{tb.format_exc()}")
