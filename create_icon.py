from PIL import Image, ImageDraw

def draw_icon(size):
    """Рисует чёрно-белую иконку: мозг и 4 мысли."""
    if size <= 0:
        raise ValueError("Размер иконки должен быть положительным числом.")

    # Белый фон для чёрно-белого дизайна
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    black = (0, 0, 0)
    center_x = size // 2
    center_y = int(size * 0.60)

    # Контур схематичного мозга: внешняя форма + внутренние дуги-извилины
    brain_w = int(size * 0.66)
    brain_h = int(size * 0.40)
    brain_box = [
        center_x - brain_w // 2,
        center_y - brain_h // 2,
        center_x + brain_w // 2,
        center_y + brain_h // 2,
    ]
    # На маленьких размерах линии должны быть чуть толще для читаемости.
    stroke = max(1, round(size / 12))
    detail_stroke = max(1, stroke - 1)
    draw.rounded_rectangle(brain_box, radius=int(size * 0.12), outline=black, width=stroke)

    # Вертикальный разделитель полушарий
    draw.line(
        [(center_x, brain_box[1] + int(size * 0.04)), (center_x, brain_box[3] - int(size * 0.05))],
        fill=black,
        width=detail_stroke,
    )

    # Внутренние извилины: для маленьких размеров оставляем только 2 дуги.
    left_arc_box = [center_x - int(size * 0.25), center_y - int(size * 0.11), center_x - int(size * 0.06), center_y + int(size * 0.02)]
    right_arc_box = [center_x + int(size * 0.06), center_y - int(size * 0.11), center_x + int(size * 0.25), center_y + int(size * 0.02)]
    draw.arc(left_arc_box, start=10, end=350, fill=black, width=detail_stroke)
    draw.arc(right_arc_box, start=190, end=530, fill=black, width=detail_stroke)

    if size >= 48:
        # Доп. мелкие детали добавляем только на средних и больших иконках.
        inner_left = [center_x - int(size * 0.16), center_y - int(size * 0.04), center_x - int(size * 0.02), center_y + int(size * 0.08)]
        inner_right = [center_x + int(size * 0.02), center_y - int(size * 0.04), center_x + int(size * 0.16), center_y + int(size * 0.08)]
        draw.arc(inner_left, start=30, end=330, fill=black, width=detail_stroke)
        draw.arc(inner_right, start=210, end=510, fill=black, width=detail_stroke)

    # 4 "мысли" над мозгом
    thought_radii = [
        max(1, int(size * 0.055)),
        max(1, int(size * 0.048)),
        max(1, int(size * 0.048)),
        max(1, int(size * 0.055)),
    ]
    thought_points = [
        (center_x - int(size * 0.24), int(size * 0.16)),
        (center_x - int(size * 0.08), int(size * 0.11)),
        (center_x + int(size * 0.08), int(size * 0.11)),
        (center_x + int(size * 0.24), int(size * 0.16)),
    ]
    for (tx, ty), r in zip(thought_points, thought_radii):
        draw.ellipse([tx - r, ty - r, tx + r, ty + r], outline=black, width=detail_stroke)

    # Связь от мозга к мыслям
    draw.line([(center_x, brain_box[1]), (center_x, int(size * 0.2))], fill=black, width=detail_stroke)

    return img

# Размеры иконки
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
icons = [draw_icon(s) for s, _ in sizes]

# Изображения уже в RGB режиме, просто убеждаемся
rgb_icons = []
for icon in icons:
    # Убеждаемся, что изображение в RGB режиме (не палитра)
    if icon.mode != "RGB":
        rgb_img = icon.convert("RGB")
    else:
        rgb_img = icon
    rgb_icons.append(rgb_img)

# Сохранение с явным указанием формата
try:
    rgb_icons[0].save(
        "app.ico",
        format="ICO",
        sizes=sizes,
        append_images=rgb_icons[1:]
    )
    print("✅ Иконка 'app.ico' создана!")
    print("   Дизайн: чёрно-белый схематичный мозг и 4 мысли")
    print("   Цвета: белый фон, чёрные линии")
except Exception as e:
    print(f"❌ Ошибка при сохранении: {e}")
    # Альтернативный способ - сохранить каждое изображение отдельно
    print("Попытка альтернативного метода сохранения...")
    rgb_icons[0].save("app.ico", format="ICO")
    print("✅ Иконка 'app.ico' создана (только один размер)")