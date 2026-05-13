import sys
import os
try:
    from docx import Document
except ImportError:
    print("Error: python-docx library not found. Please install it using 'pip install python-docx'")
    sys.exit(1)

def docx_to_markdown(input_path, output_path):
    try:
        doc = Document(input_path)
        content = []
        
        for para in doc.paragraphs:
            # Simple mapping of styles to markdown
            if para.style.name.startswith('Heading 1'):
                content.append(f"# {para.text}")
            elif para.style.name.startswith('Heading 2'):
                content.append(f"## {para.text}")
            elif para.style.name.startswith('Heading 3'):
                content.append(f"### {para.text}")
            else:
                content.append(para.text)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n\n".join(content))
            
        print(f"Successfully converted {input_path} to {output_path}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python docx_to_markdown.py <input_docx> <output_md>")
    else:
        docx_to_markdown(sys.argv[1], sys.argv[2])
