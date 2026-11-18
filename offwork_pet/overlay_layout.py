"""Screen-relative desktop elements and parameter registry, independent of Qt."""
from copy import deepcopy
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Parameter:
    title: str
    default: object
    minimum: float = 0
    maximum: float = 10000
    kind: str = 'int'


COMMON = {
    'screen': Parameter('显示器', '', kind='screen'),
    'x': Parameter('横向位置', None, 0, 32768),
    'y': Parameter('纵向位置', None, 0, 32768),
    'width': Parameter('区域宽度 / 像素', 360, 80, 3840),
    'height': Parameter('区域高度 / 像素', 240, 28, 2160),
    'font_size': Parameter('字号', 10, 6, 36),
    'opacity': Parameter('文字不透明度 %', 65, 10, 100),
    'color': Parameter('文字颜色', '#78808C', kind='color'),
}


@dataclass(frozen=True)
class Element:
    title: str
    sample: tuple[str, ...]
    parameters: dict[str, Parameter]
    default_x: float
    default_y: float
    single_line: bool = False


# New elements register their parameters and samples here; the editor builds
# controls and independent preview handles from this registry.
ELEMENTS = {
    'chat_messages': Element('聊天消息', ('搭子：这是一条预览消息，不会发送。', '搭子：拖动这个区域调整位置。'), {
        **COMMON,
        'seconds': Parameter('总展示时长 / 秒', 5.0, .5, 60, 'float'),
        'fade_seconds': Parameter('淡出时长 / 秒', 1.0, 0, 60, 'float'),
        'max_messages': Parameter('最大显示数量 / 条', 3, 1, 20),
        'gap': Parameter('消息间距', 6, 0, 40),
    }, .65, .7),
    'chat_input': Element('输入光标', ('在这里输入文字，回车发送',), {
        **COMMON, 'height': Parameter('区域高度 / 像素', 28, 28, 1080),
    }, .65, .85, True),
}


def element_settings(layout, key):
    return {**{name: parameter.default for name, parameter in ELEMENTS[key].parameters.items()},
            **layout.get(key, {})}


def validate_layout(layout):
    if not isinstance(layout, dict) or len(layout) > 64:
        raise ValueError('桌面元素布局配置无效')
    for key, values in layout.items():
        if key not in ELEMENTS or not isinstance(values, dict):
            raise ValueError('未知桌面元素或参数格式无效')
        for name, value in values.items():
            parameter = ELEMENTS[key].parameters.get(name)
            if parameter is None:
                raise ValueError('未知桌面元素参数')
            if name in ('x', 'y') and value is None:
                continue
            if parameter.kind == 'int':
                valid = type(value) is int and parameter.minimum <= value <= parameter.maximum
            elif parameter.kind == 'float':
                valid = type(value) in (int, float) and parameter.minimum <= value <= parameter.maximum
            elif parameter.kind == 'screen':
                valid = isinstance(value, str) and len(value) <= 128
            else:
                valid = isinstance(value, str) and bool(re.fullmatch(r'#[0-9a-fA-F]{6}', value))
            if not valid:
                raise ValueError(f'{ELEMENTS[key].title}：{parameter.title}无效')
    return layout


def copy_layout(layout):
    return deepcopy(validate_layout(layout))
