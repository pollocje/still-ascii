import sys
import argparse
import math
import shutil
from PIL import Image, ImageFilter, ImageEnhance
import tkinter as tk
from tkinter import filedialog

ASCII_CHARS = '$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/|()1{}[]?-_+~<>i!lI;:,"^`. '
BLOCK_CHARS = ' ░▒▓█'

MODES = ('ascii', 'color', 'blocks', 'colorblocks', 'braille', 'halfblock', 'retro', 'art')


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _apply_gamma(pixels, gamma):
    if gamma == 1.0:
        return pixels
    return [(p / 255) ** gamma * 255 for p in pixels]


def _sobel(pixels_rgb, width, height):
    def L(x, y):
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        r, g, b = pixels_rgb[y * width + x]
        return _luma(r, g, b)

    gx_list, gy_list = [], []
    for y in range(height):
        for x in range(width):
            gx = (L(x+1,y-1) + 2*L(x+1,y) + L(x+1,y+1)
                  - L(x-1,y-1) - 2*L(x-1,y) - L(x-1,y+1))
            gy = (L(x-1,y+1) + 2*L(x,y+1) + L(x+1,y+1)
                  - L(x-1,y-1) - 2*L(x,y-1) - L(x+1,y-1))
            gx_list.append(gx)
            gy_list.append(gy)
    return gx_list, gy_list


def _gradient_char(gx, gy):
    ax, ay = abs(gx), abs(gy)
    if ax > 2 * ay:
        return '|'
    elif ay > 2 * ax:
        return '-'
    elif (gx > 0) == (gy > 0):
        return '/'
    else:
        return '\\'


def _build_char_grid(pixels_luma, width, height, chars, dither):
    n = len(chars) - 1
    grid = [[None] * width for _ in range(height)]
    if dither:
        arr = [list(pixels_luma[i * width:(i + 1) * width]) for i in range(height)]
        for i in range(height):
            for j in range(width):
                old = max(0.0, min(255.0, arr[i][j]))
                idx = max(0, min(n, int(round(old / 255 * n))))
                grid[i][j] = chars[idx]
                err = old - (idx / n * 255)
                if j + 1 < width:
                    arr[i][j + 1] += err * 7 / 16
                if i + 1 < height:
                    if j > 0:
                        arr[i + 1][j - 1] += err * 3 / 16
                    arr[i + 1][j] += err * 5 / 16
                    if j + 1 < width:
                        arr[i + 1][j + 1] += err * 1 / 16
    else:
        for i in range(height):
            for j in range(width):
                idx = max(0, min(n, int(pixels_luma[i * width + j] / 255 * n)))
                grid[i][j] = chars[idx]
    return grid


def _overlay_edges(grid, gx_list, gy_list, width, height, threshold):
    for i in range(height):
        for j in range(width):
            flat = i * width + j
            gx, gy = gx_list[flat], gy_list[flat]
            if math.sqrt(gx * gx + gy * gy) > threshold:
                grid[i][j] = _gradient_char(gx, gy)


def _render_halfblock(img, width, contrast, sharpen):
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    orig_w, orig_h = img.size
    pixel_h = max(2, int(width * orig_h / orig_w))
    if pixel_h % 2:
        pixel_h += 1
    pixels = list(img.resize((width, pixel_h), Image.LANCZOS).convert('RGB').getdata())
    lines = []
    for i in range(pixel_h // 2):
        row = ''
        for j in range(width):
            r1, g1, b1 = pixels[(i * 2) * width + j]
            r2, g2, b2 = pixels[(i * 2 + 1) * width + j]
            row += f'\033[38;2;{r1};{g1};{b1}m\033[48;2;{r2};{g2};{b2}m▀\033[0m'
        lines.append(row)
    return '\n'.join(lines)


_BRAILLE_DOTS = [
    (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04),
    (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
    (0, 3, 0x40), (1, 3, 0x80),
]


def _render_braille(img, width, color, contrast, invert):
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    orig_w, orig_h = img.size
    height = max(1, int(width * orig_h / orig_w * 0.45))
    px_w, px_h = width * 2, height * 4
    pixels = list(img.resize((px_w, px_h), Image.LANCZOS).convert('RGB').getdata())

    lines = []
    for row in range(height):
        line = []
        for col in range(width):
            bits = 0
            rs = gs = bs = 0
            for dc, dr, bit in _BRAILLE_DOTS:
                r, g, b = pixels[(row * 4 + dr) * px_w + col * 2 + dc]
                if (_luma(r, g, b) > 128) != invert:
                    bits |= bit
                rs += r; gs += g; bs += b
            ch = chr(0x2800 + bits)
            if color:
                line.append(f'\033[38;2;{rs//8};{gs//8};{bs//8}m{ch}\033[0m')
            else:
                line.append(ch)
        lines.append(''.join(line))
    return '\n'.join(lines)


def _render_retro(img, width, contrast, sharpen, invert, amber=False):
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    orig_w, orig_h = img.size
    height = max(1, int(width * orig_h / orig_w * 0.45))
    img_resized = img.resize((width, height), Image.LANCZOS)
    pixels_luma = [_luma(r, g, b) for r, g, b in img_resized.convert('RGB').getdata()]
    chars = ASCII_CHARS[::-1] if invert else ASCII_CHARS
    n = len(chars) - 1
    br, bg, bb = (255, 176, 0) if amber else (0, 220, 70)
    lines = []
    for i in range(height):
        row = []
        for j in range(width):
            luma = pixels_luma[i * width + j]
            ch = chars[max(0, min(n, int(luma / 255 * n)))]
            scale = luma / 255
            row.append(f'\033[38;2;{int(br*scale)};{int(bg*scale)};{int(bb*scale)}m{ch}\033[0m')
        lines.append(''.join(row))
    return '\n'.join(lines)


def _frame_to_ascii(img, width=100, invert=False, color=False, sharpen=False,
                    contrast=1.0, gamma=1.0, dither=False, blocks=False,
                    halfblock=False, edges=False, braille=False, retro=False):
    if halfblock:
        return _render_halfblock(img, width, contrast, sharpen)
    if braille:
        return _render_braille(img, width, color, contrast, invert)
    if retro:
        return _render_retro(img, width, contrast, sharpen, invert)

    orig_w, orig_h = img.size
    height = max(1, int(width * orig_h / orig_w * 0.45))

    img_resized = img.resize((width, height), Image.LANCZOS)
    if contrast != 1.0:
        img_resized = ImageEnhance.Contrast(img_resized).enhance(contrast)
    if sharpen:
        img_resized = img_resized.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))

    chars = BLOCK_CHARS if blocks else ASCII_CHARS
    if invert:
        chars = chars[::-1]

    pixels_rgb = list(img_resized.convert('RGB').getdata())
    pixels_luma = _apply_gamma([_luma(r, g, b) for r, g, b in pixels_rgb], gamma)

    grid = _build_char_grid(pixels_luma, width, height, chars, dither)

    if edges:
        gx_list, gy_list = _sobel(pixels_rgb, width, height)
        mags = [math.sqrt(gx * gx + gy * gy) for gx, gy in zip(gx_list, gy_list)]
        threshold = sorted(mags)[int(len(mags) * 0.80)]
        _overlay_edges(grid, gx_list, gy_list, width, height, threshold)

    lines = []
    for i in range(height):
        row_parts = []
        for j in range(width):
            ch = grid[i][j]
            if color:
                r, g, b = pixels_rgb[i * width + j]
                row_parts.append(f'\033[38;2;{r};{g};{b}m{ch}\033[0m')
            else:
                row_parts.append(ch)
        lines.append(''.join(row_parts))
    return '\n'.join(lines)


def image_to_ascii(image_path, **kwargs):
    try:
        img = Image.open(image_path)
    except FileNotFoundError:
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error opening image: {e}", file=sys.stderr)
        sys.exit(1)
    return _frame_to_ascii(img, **kwargs)


def _is_animated_gif(path):
    try:
        img = Image.open(path)
        img.seek(1)
        return True
    except (EOFError, AttributeError):
        return False


def _extract_gif_frames(img):
    frames, durations = [], []
    try:
        while True:
            frame_rgba = img.convert('RGBA')
            bg = Image.new('RGB', img.size, (0, 0, 0))
            bg.paste(frame_rgba, mask=frame_rgba.split()[3])
            frames.append(bg)
            durations.append(max(img.info.get('duration', 100), 20) / 1000.0)
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    return frames, durations


def _play_gif(image_path, **kwargs):
    import time
    try:
        img = Image.open(image_path)
    except Exception as e:
        print(f"Error opening image: {e}", file=sys.stderr)
        sys.exit(1)

    frames_pil, durations = _extract_gif_frames(img)
    print(f'Rendering {len(frames_pil)} frames...', flush=True)
    frames_ascii = [_frame_to_ascii(f, **kwargs) for f in frames_pil]
    n_lines = frames_ascii[0].count('\n') + 2

    print('\033[2J\033[H', end='', flush=True)
    try:
        while True:
            for frame, duration in zip(frames_ascii, durations):
                print('\033[H' + frame, end='', flush=True)
                time.sleep(duration)
    except KeyboardInterrupt:
        print(f'\033[{n_lines};1H\n')
        print('Stopped.')


def _pick_file_gui():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
    path = filedialog.askopenfilename(
        title='Select an image',
        filetypes=[('Image files', '*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff'), ('All files', '*.*')],
        parent=root
    )
    root.destroy()
    if not path:
        sys.exit(0)
    return path


def _pick_mode_gui():
    result = {'mode': None}

    root = tk.Tk()
    root.title('Select Mode')
    root.configure(bg='#1e1e1e')
    root.resizable(False, False)
    root.attributes('-topmost', True)

    tk.Label(root, text='Choose a rendering mode:', bg='#1e1e1e', fg='white',
             font=('Segoe UI', 11)).pack(pady=(16, 8), padx=20)

    descriptions = {
        'ascii':       'Plain ASCII characters',
        'color':       'ASCII with terminal true-color',
        'blocks':      'Unicode block characters (░▒▓█)',
        'colorblocks': 'Colored Unicode block characters',
        'braille':     'High-res colored braille characters (⠿)',
        'halfblock':   'High-res color via half-block (▀)',
        'retro':       'Monochrome green phosphor CRT look',
        'art':         'Color + edges + dithering + sharpening',
    }

    for mode in MODES:
        btn = tk.Button(
            root,
            text=f'{mode}  —  {descriptions[mode]}',
            anchor='w',
            bg='#2d2d2d', fg='white', activebackground='#444', activeforeground='white',
            font=('Segoe UI', 10), relief='flat', padx=12, pady=6, cursor='hand2',
            command=lambda m=mode: (result.update({'mode': m}), root.destroy())
        )
        btn.pack(fill='x', padx=20, pady=3)

    tk.Button(root, text='Cancel', bg='#2d2d2d', fg='#888', activebackground='#444',
              font=('Segoe UI', 9), relief='flat', padx=12, pady=4, cursor='hand2',
              command=lambda: (result.update({'mode': None}), root.destroy())
              ).pack(pady=(8, 16), padx=20)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f'+{x}+{y}')
    root.mainloop()

    if result['mode'] is None:
        sys.exit(0)
    return result['mode']


def _build_kwargs(mode, width, invert):
    return dict(
        width=width,
        invert=invert,
        color=mode in ('color', 'art', 'colorblocks', 'braille'),
        sharpen=mode in ('art', 'retro'),
        contrast=1.3 if mode == 'art' else 1.0,
        dither=mode in ('art', 'blocks', 'colorblocks'),
        blocks=mode in ('blocks', 'colorblocks'),
        halfblock=mode == 'halfblock',
        edges=mode == 'art',
        braille=mode == 'braille',
        retro=mode == 'retro',
    )


def _prompt_mode_switch(current_mode):
    import msvcrt
    labels = '  '.join(f'[{i+1}] {m}{"  <--" if m == current_mode else ""}' for i, m in enumerate(MODES))
    print(f'\n{labels}  [Q] quit')
    print('Switch mode: ', end='', flush=True)
    while True:
        key = msvcrt.getch()
        if key in (b'q', b'Q', b'\r', b'\n', b'\x1b'):
            return None
        try:
            n = int(key.decode())
            if 1 <= n <= len(MODES):
                return MODES[n - 1]
        except (ValueError, UnicodeDecodeError):
            pass


def main():
    parser = argparse.ArgumentParser(
        description='Convert an image to ASCII art.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''modes:
  ascii        plain ASCII characters (default)
  color        ASCII with terminal true-color
  blocks       Unicode block characters (░▒▓█)
  colorblocks  colored Unicode block characters
  halfblock    high-res color via half-block (▀) — best in color terminals
  art          color + edges + dithering + sharpening

examples:
  python ascii_image.py photo.jpg
  python ascii_image.py photo.jpg color
  python ascii_image.py photo.jpg halfblock
  python ascii_image.py photo.jpg art --width 120'''
    )
    parser.add_argument('image', nargs='?', default=None, help='Path to the image file')
    parser.add_argument('mode', nargs='?', default='ascii', choices=MODES,
                        help='Rendering mode (default: ascii)')
    parser.add_argument('--width', type=int, default=None,
                        help='Width in characters (default: terminal width)')
    parser.add_argument('--invert', action='store_true',
                        help='Invert brightness (for light-background terminals)')

    args = parser.parse_args()

    gui_mode = args.image is None
    if gui_mode:
        args.image = _pick_file_gui()
        args.mode = _pick_mode_gui()

    width = args.width or shutil.get_terminal_size((100, 40)).columns
    mode = args.mode
    is_gif = _is_animated_gif(args.image)

    while True:
        kwargs = _build_kwargs(mode, width, args.invert)
        if is_gif:
            _play_gif(args.image, **kwargs)
        else:
            print(image_to_ascii(args.image, **kwargs))

        if not gui_mode:
            break

        new_mode = _prompt_mode_switch(mode)
        if new_mode is None:
            break
        mode = new_mode
        print()


if __name__ == '__main__':
    main()
