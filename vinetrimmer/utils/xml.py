from typing import Union

from lxml import etree
from lxml.etree import ElementTree


def load_xml(xml: Union[str, bytes]) -> ElementTree:
    if not isinstance(xml, bytes):
        xml = xml.encode("utf8")
    root = etree.fromstring(xml)
    for elem in root.getiterator():
        if not hasattr(elem.tag, "find"):
            # e.g. comment elements
            continue
        elem.tag = etree.QName(elem).localname
    etree.cleanup_namespaces(root)
    return root
