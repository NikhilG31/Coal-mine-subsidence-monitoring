import re
import os
import subprocess

with open('docs/AITM_ECE_Mini_Project_Synopsis.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract styles
css_m = re.search(r'<style>(.*?)</style>', content, re.DOTALL)
css = css_m.group(1)

# Split by <!-- ==================== PAGE X ==================== -->
parts = re.split(r'<!-- ==================== PAGE \d+ ==================== -->', content)
# parts[0] is header stuff, parts[1..4] are pages
print('Number of parts:', len(parts))

chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'

for i in range(1, len(parts)):
    page_content = parts[i].strip()
    if page_content.endswith('</body>') or page_content.endswith('</html>'):
        page_content = re.sub(r'</body>\s*</html>', '', page_content).strip()
    
    doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
{css}
body {{ margin: 0; padding: 0; background: #ffffff; }}
.page {{ width: 210mm; height: 297mm; margin: 0 auto; box-shadow: none; border: none; }}
</style>
</head>
<body>
{page_content}
</body>
</html>"""
    p_html_path = os.path.abspath(f'docs/p_{i}.html')
    with open(p_html_path, 'w', encoding='utf-8') as pf:
        pf.write(doc)
    
    img_path = os.path.abspath(f'docs/page_{i}.png')
    subprocess.run([
        chrome,
        '--headless=new',
        '--disable-gpu',
        '--no-sandbox',
        '--window-size=860,1215',
        f'--screenshot={img_path}',
        p_html_path
    ])
    print(f'Rendered Page {i} -> {img_path}')
