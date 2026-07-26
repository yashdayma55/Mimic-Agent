from pywinauto import Desktop

elem = Desktop(backend="uia").from_point(500, 500)
info = elem.element_info
print("Name:", info.name)
print("Type:", info.control_type)
print("Rect:", info.rectangle)