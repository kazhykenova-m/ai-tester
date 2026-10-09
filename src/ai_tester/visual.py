"""Pixel comparison without resizing or automatic alignment."""

from PIL import Image, ImageDraw, ImageChops

MAX_PIXELS = 16000000


def inspect_png(path):
    with Image.open(path) as image:
        if image.format != "PNG" or image.width * image.height > MAX_PIXELS:
            raise ValueError("Нужен PNG до 16 миллионов пикселей")
        image.verify()
    with Image.open(path) as image:
        return image.size


def compare(reference, screenshot, output, threshold=30, min_area=100, exclude=()):
    inspect_png(reference)
    inspect_png(screenshot)
    if not 0 <= threshold <= 255 or min_area < 1:
        raise ValueError("Некорректные параметры сравнения")
    with Image.open(reference) as ref, Image.open(screenshot) as shot:
        a, b = ref.convert("RGB"), shot.convert("RGB")
    if a.size != b.size:
        raise ValueError(
            "Несовместимые размеры: макет %s, скриншот %s. Экспортируйте фрейм 1×; изображения не растягиваются."
            % (a.size, b.size)
        )
    diff = ImageChops.difference(a, b)
    channels = diff.split()
    mask = ImageChops.lighter(
        ImageChops.lighter(channels[0], channels[1]), channels[2]
    ).point(lambda p: 255 if p > threshold else 0)
    draw = ImageDraw.Draw(mask)
    for box in exclude:
        draw.rectangle(box, fill=0)
    # Connected components make the minimum area apply to each discrepancy.
    pixels = mask.load()

    boxes, changed = [], 0
    for y in range(mask.height):
        for x in range(mask.width):
            if not pixels[x, y]:
                continue
            todo = [(x, y)]
            pixels[x, y] = 0
            count = 0
            left = right = x
            top = bottom = y
            while todo:
                px, py = todo.pop()
                count += 1
                left = min(left, px)
                right = max(right, px)
                top = min(top, py)
                bottom = max(bottom, py)
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if (
                        0 <= nx < mask.width
                        and 0 <= ny < mask.height
                        and pixels[nx, ny]
                    ):
                        pixels[nx, ny] = 0
                        todo.append((nx, ny))
            if count >= min_area:
                changed += count
                boxes.append((left, top, right, bottom))
    highlight = ImageDraw.Draw(b)
    for box in boxes:
        highlight.rectangle(box, outline="red", width=3)
    b.save(output)
    return {
        "changed_pixels": changed,
        "ratio": changed / (a.width * a.height),
        "regions": boxes,
        "threshold": threshold,
        "min_area": min_area,
        "exclude": list(exclude),
    }
