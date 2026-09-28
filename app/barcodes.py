"""Genera etiquetas vectoriales usando el único código del producto."""
from base64 import b64encode

from barcode import Code128
from barcode.writer import SVGWriter


def barcode_image(code):
    # Code 128 conserva números, ceros iniciales, letras y signos ASCII.
    # No reemplazar caracteres: la lectura debe coincidir con el código guardado.
    if not code or any(not 32 <= ord(char) <= 126 for char in code):
        raise ValueError("Para imprimir el código de barras, el código del producto debe contener números, letras sin tildes o signos comunes.")
    svg = Code128(code, writer=SVGWriter()).render({
        "module_width": 0.25,
        "module_height": 20,
        "quiet_zone": 6.5,
        "font_size": 0,
        "write_text": False,
        "background": "white",
        "foreground": "black",
    })
    return "data:image/svg+xml;base64," + b64encode(svg).decode("ascii")
