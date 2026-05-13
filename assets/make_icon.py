"""生成 Prompt Help 应用图标（Phase 18 重新设计）。

设计思路：
- 黑色圆角方形（参考 macOS Big Sur / iOS 风）
- 白色 "Ph" 字样（小写 h 避免 PH 看起来像 "ph" 单词）
- 右下角金色 #c7a35a 高光圆点——呼应 spotlight 引导特效
- 保持极简风（不画复杂图形）

输出：
- assets/icon.ico（含 16/32/48/64/128/256 六档）
- assets/icon-256.png（用于 About / installer 横幅）
- assets/icon-1024.png（高清版，用于商店 / 上架）
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

# 颜色 token（与 theme.py 对齐）
BG = (10, 10, 10, 255)          # INK_900
FG = (250, 250, 250, 255)       # SURFACE
ACCENT = (199, 163, 90, 255)    # 金色（c7a35a，与 spotlight 边框一致）


def _font(px: int) -> ImageFont.ImageFont:
    """优先 SF Pro / Inter / Helvetica / 系统 Bold。"""
    for name in (
        "InterDisplay-Black.ttf", "Inter-Black.ttf",
        "SF-Pro-Display-Black.otf", "Helvetica-Bold.ttf",
        "seguibl.ttf",         # Windows Segoe UI Black
        "seguibld.ttf",        # Segoe UI Bold
        "arialbd.ttf",         # Arial Bold
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    return ImageFont.load_default()


def _rounded_rect(size: int, radius: int, fill: tuple) -> Image.Image:
    """画一张 size×size 的圆角方形（透明背景）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)],
        radius=radius,
        fill=fill,
    )
    return img


def _draw_icon(size: int) -> Image.Image:
    """画 size×size 完整图标。"""
    # 圆角比例 ~22%（接近 macOS Big Sur 标准）
    radius = max(2, int(size * 0.22))
    img = _rounded_rect(size, radius, BG)
    draw = ImageDraw.Draw(img)

    # 主字 "Ph"
    # 字号占图标高度 56%，让上下留白舒适
    f = _font(int(size * 0.56))
    text = "Ph"
    bbox = draw.textbbox((0, 0), text, font=f)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # 微调：往左偏 4%，往上偏 2%，视觉居中更准
    x = (size - tw) // 2 - bbox[0] - int(size * 0.02)
    y = (size - th) // 2 - bbox[1] - int(size * 0.04)
    draw.text((x, y), text, font=f, fill=FG)

    # 右下角金色高光圆点（呼应 spotlight 引导）
    # 直径占图标 14%，距离右下边 17%
    dot_d = max(2, int(size * 0.14))
    margin = int(size * 0.17)
    dot_x = size - margin - dot_d
    dot_y = size - margin - dot_d
    draw.ellipse(
        [(dot_x, dot_y), (dot_x + dot_d, dot_y + dot_d)],
        fill=ACCENT,
    )

    return img


def main() -> None:
    assets = Path(__file__).resolve().parent
    assets.mkdir(parents=True, exist_ok=True)

    # 生成所有尺寸
    images = [_draw_icon(w) for w, _ in SIZES]

    # 写 .ico
    ico_path = assets / "icon.ico"
    images[0].save(
        ico_path, format="ICO", sizes=SIZES,
        append_images=images[1:],
    )
    print(f"OK {ico_path}")

    # 写 256 PNG（About / installer 横幅用）
    png_path = assets / "icon-256.png"
    images[0].save(png_path, format="PNG")
    print(f"OK {png_path}")

    # 写 1024 PNG（高清版，未来商店 / 营销用）
    big = _draw_icon(1024)
    big_path = assets / "icon-1024.png"
    big.save(big_path, format="PNG")
    print(f"OK {big_path}")


if __name__ == "__main__":
    main()
