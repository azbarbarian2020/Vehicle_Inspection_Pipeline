"""
PyMuPDF-based image extraction from Vehicle Inspection PDF Picture Attachments.
Uses spatial Y-coordinate positioning to correctly map images to line numbers.
"""
import re
import fitz  # PyMuPDF


def find_picture_attachments_start(doc):
    """Find the page index where Picture Attachments section begins."""
    for pn in range(doc.page_count):
        text = doc[pn].get_text()
        if 'Picture Attachments' in text:
            return pn
    return None


def extract_image_assignments(doc, pic_start_page):
    """
    Parse Picture Attachments pages using spatial Y-coordinates to map images
    to their correct line numbers.

    Uses get_text('dict') to get positioned blocks, then interleaves LINE
    and IMG entries by Y-position. Images appearing after a LINE entry
    belong to that line until the next LINE entry.

    Returns: dict of line_num -> [(page_idx, img_idx_on_page), ...]
    """
    all_entries = []  # list of ('LINE', line_num, pass_code) or ('IMG', page_idx, img_idx)

    for pn in range(pic_start_page, doc.page_count):
        page = doc[pn]
        blocks = page.get_text('dict')['blocks']
        img_list = page.get_images(full=True)

        # Sort blocks by Y position
        sorted_blocks = sorted(blocks, key=lambda x: x['bbox'][1])
        img_counter = 0

        for b in sorted_blocks:
            if b['type'] == 0:  # text block
                text = ''
                for line in b['lines']:
                    for span in line['spans']:
                        text += span['text']
                text = text.strip()
                # Match line number + pass code (e.g. "1.08F" or "3.12Fdamaged...")
                m = re.match(r'^(\d+\.\d+)([FPN])', text)
                if m:
                    all_entries.append(('LINE', m.group(1), m.group(2)))
            elif b['type'] == 1:  # image block
                if img_counter < len(img_list):
                    all_entries.append(('IMG', pn, img_counter))
                    img_counter += 1

    # Assign images to line numbers: images after a LINE entry belong to it
    assignments = {}
    current_line = None

    for entry in all_entries:
        if entry[0] == 'LINE':
            current_line = entry[1]
            if current_line not in assignments:
                assignments[current_line] = []
        elif entry[0] == 'IMG' and current_line:
            assignments[current_line].append((entry[1], entry[2]))

    return assignments


def extract_failure_images(pdf_path, failed_line_nums):
    """
    Extract images for failed line items from a PDF file.

    Args:
        pdf_path: Path to the PDF file (local)
        failed_line_nums: Set of line numbers that failed (e.g. {'1.08', '3.12'})

    Returns:
        dict of line_num -> [{'data': bytes, 'ext': str, 'seq': int}, ...]
    """
    doc = fitz.open(pdf_path)
    pic_start = find_picture_attachments_start(doc)

    if pic_start is None:
        doc.close()
        return {}

    assignments = extract_image_assignments(doc, pic_start)
    result = {}

    for line_num in failed_line_nums:
        img_refs = assignments.get(line_num, [])
        if not img_refs:
            continue

        result[line_num] = []
        for seq, (page_idx, img_idx) in enumerate(img_refs, 1):
            page = doc[page_idx]
            img_list = page.get_images(full=True)

            if img_idx >= len(img_list):
                continue

            xref = img_list[img_idx][0]
            pix = fitz.Pixmap(doc, xref)

            # Convert CMYK to RGB if needed
            if pix.n > 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            # Get image bytes as PNG
            img_bytes = pix.tobytes("png")
            pix = None

            result[line_num].append({
                'data': img_bytes,
                'ext': 'png',
                'seq': seq
            })

    doc.close()
    return result
