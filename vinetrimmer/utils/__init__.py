import ast
from typing import Optional, Sequence, Union

from langcodes import Language, closest_match
from pymp4.parser import Box

from vinetrimmer.constants import LANGUAGE_MAX_DISTANCE
from vinetrimmer.utils.Widevine.cdm import Cdm  # noqa: F401
from vinetrimmer.utils.Widevine.protos.widevine_pb2 import WidevineCencHeader  # noqa: F401


class FPS(ast.NodeVisitor):
    def visit_BinOp(self, node: ast.BinOp) -> float:
        if isinstance(node.op, ast.Div):
            return self.visit(node.left) / self.visit(node.right)
        else:
            raise ValueError(f'Invalid operation: {node.op}')

    def visit_Num(self, node: ast.Num) -> complex:
        return node.n

    def visit_Expr(self, node: ast.Expr) -> float:
        return self.visit(node.value)

    @classmethod
    def parse(cls, expr: str) -> float:
        return cls().visit(ast.parse(expr).body[0])


def get_boxes(data: bytes, box_type: bytes, as_bytes: bool = False) -> Box:
    """Scan a byte array for a wanted box, then parse and yield each find."""
    # using slicing to get to the wanted box is done because parsing the entire box and recursively
    # scanning through each box and its children often wouldn't scan far enough to reach the wanted box.
    # since it doesnt care what child box the wanted box is from, this works fine.
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data must be bytes")
    while True:
        try:
            index = data.index(box_type)
        except ValueError:
            break
        if index < 0:
            break
        if index > 4:
            index -= 4  # size is before box type and is 4 bytes long
        data = data[index:]
        try:
            box = Box.parse(data)
        except IOError:
            # TODO: Does this miss any data we may need?
            break
        if as_bytes:
            box = Box.build(box)
        yield box


def is_close_match(language: Union[str, Language], languages: Optional[Sequence[Union[str, Language, None]]]) -> bool:
    if not languages:
        return False
    languages = list(map(str, [x for x in languages if x]))
    return closest_match(language, languages)[1] <= LANGUAGE_MAX_DISTANCE


def get_closest_match(language: Union[str, Language], languages: Sequence[Union[str, Language]]) -> Optional[Language]:
    match, distance = closest_match(language, list(map(str, languages)))
    if distance > LANGUAGE_MAX_DISTANCE:
        return None
    return Language.get(match)
