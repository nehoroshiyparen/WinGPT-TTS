"""Read-only diagnostic of the active ChatGPT accessibility document."""

import json
import sys

from pywinauto.uia_defines import IUIA

from wgtts.chat.reader import ChatReader


def describe(element):
    info = element.element_info
    native = info.element
    return {
        "type": info.control_type,
        "name": info.name[:100],
        "id": info.automation_id,
        "class": info.class_name[:180],
        "role": native.CurrentAriaRole,
        "aria": native.CurrentAriaProperties,
        "runtime_id": list(info.runtime_id),
        "rect": str(info.rectangle),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    context = ChatReader().connect()
    document = context.document
    nodes = document.descendants()
    constants = IUIA().UIA_dll
    result = {"document": describe(document), "nodes": len(nodes)}
    try:
        result["document_value"] = document.legacy_properties().get("Value")
    except Exception as error:
        result["document_value_error"] = type(error).__name__
    headings = [
        node
        for node in nodes
        if node.window_text() in ("Вы сказали:", "ChatGPT сказал:")
        and node.element_info.element.CurrentAriaRole == "heading"
    ]
    result["headings"] = [describe(node) for node in headings]
    result["scrollables"] = []
    result["containers"] = []
    for node in [document, *nodes]:
        native = node.element_info.element
        if native.GetCurrentPropertyValue(constants.UIA_IsScrollPatternAvailablePropertyId):
            item = describe(node)
            scroll = node.iface_scroll
            item["scroll"] = {
                "vertical": scroll.CurrentVerticallyScrollable,
                "percent": scroll.CurrentVerticalScrollPercent,
                "view_size": scroll.CurrentVerticalViewSize,
            }
            result["scrollables"].append(item)
        if native.GetCurrentPropertyValue(constants.UIA_IsItemContainerPatternAvailablePropertyId):
            result["containers"].append(describe(node))
    conversation = context.conversation
    if conversation:
        parent = conversation
        children = parent.children()
        result["conversation"] = describe(parent)
        result["conversation_children"] = len(children)
        result["first_children"] = [describe(node) for node in children[:14]]
        result["last_children"] = [describe(node) for node in children[-20:]]
        result["ancestors"] = []
        for heading in headings[:2] + headings[-2:]:
            chain = []
            ancestor = heading
            for _ in range(5):
                chain.append(describe(ancestor))
                ancestor = ancestor.parent()
            result["ancestors"].append(chain)
    result["selected_nodes"] = [
        describe(node)
        for node in nodes
        if "selected=true" in node.element_info.element.CurrentAriaProperties
        or "current=true" in node.element_info.element.CurrentAriaProperties
    ]
    result["jump_buttons"] = [
        describe(node)
        for node in nodes
        if node.element_info.control_type == "Button"
        and node.window_text().startswith("Перейти к сообщению пользователя")
    ]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
