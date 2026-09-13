use std::fs;
use std::path::Path;

fn ensure_windows_icon() {
    let icon_path = Path::new("icons/icon.ico");
    if icon_path.exists() {
        return;
    }

    const SIZE: usize = 32;
    let mut rgba = vec![0u8; SIZE * SIZE * 4];

    for y in 0..SIZE {
        for x in 0..SIZE {
            let index = (y * SIZE + x) * 4;
            let edge = x < 3 || y < 3 || x >= SIZE - 3 || y >= SIZE - 3;
            let waveform = matches!(x, 8 | 12 | 16 | 20 | 24)
                && y >= 8 + x.abs_diff(16) / 2
                && y < SIZE - 8 - x.abs_diff(16) / 2;

            let (r, g, b, a) = if edge {
                (27, 94, 72, 255)
            } else if waveform {
                (246, 250, 248, 255)
            } else {
                (42, 122, 91, 255)
            };

            rgba[index..index + 4].copy_from_slice(&[r, g, b, a]);
        }
    }

    let pixel_bytes = (SIZE * SIZE * 4) as u32;
    let mask_stride = ((SIZE + 31) / 32) * 4;
    let mask_bytes = (mask_stride * SIZE) as u32;
    let image_bytes = 40 + pixel_bytes + mask_bytes;
    let image_offset = 6 + 16;

    let mut ico = Vec::with_capacity(image_offset + image_bytes as usize);
    ico.extend_from_slice(&0u16.to_le_bytes());
    ico.extend_from_slice(&1u16.to_le_bytes());
    ico.extend_from_slice(&1u16.to_le_bytes());
    ico.push(SIZE as u8);
    ico.push(SIZE as u8);
    ico.push(0);
    ico.push(0);
    ico.extend_from_slice(&1u16.to_le_bytes());
    ico.extend_from_slice(&32u16.to_le_bytes());
    ico.extend_from_slice(&image_bytes.to_le_bytes());
    ico.extend_from_slice(&(image_offset as u32).to_le_bytes());

    ico.extend_from_slice(&40u32.to_le_bytes());
    ico.extend_from_slice(&(SIZE as i32).to_le_bytes());
    ico.extend_from_slice(&((SIZE * 2) as i32).to_le_bytes());
    ico.extend_from_slice(&1u16.to_le_bytes());
    ico.extend_from_slice(&32u16.to_le_bytes());
    ico.extend_from_slice(&0u32.to_le_bytes());
    ico.extend_from_slice(&pixel_bytes.to_le_bytes());
    ico.extend_from_slice(&0i32.to_le_bytes());
    ico.extend_from_slice(&0i32.to_le_bytes());
    ico.extend_from_slice(&0u32.to_le_bytes());
    ico.extend_from_slice(&0u32.to_le_bytes());

    for y in (0..SIZE).rev() {
        for x in 0..SIZE {
            let index = (y * SIZE + x) * 4;
            let [r, g, b, a] = rgba[index..index + 4] else {
                unreachable!()
            };
            ico.extend_from_slice(&[b, g, r, a]);
        }
    }

    ico.resize(ico.len() + mask_bytes as usize, 0);
    fs::write(icon_path, ico).expect("failed to generate Windows icon.ico");
}

fn main() {
    println!("cargo:rerun-if-changed=icons/icon.png");
    ensure_windows_icon();
    tauri_build::build()
}
