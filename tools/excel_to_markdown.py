import pandas as pd
import sys
import os

def excel_to_markdown(input_path, output_path):
    try:
        # Read Excel file
        # We assume the first sheet is the one to convert
        df = pd.read_excel(input_path)
        
        # Convert to Markdown
        markdown_table = df.to_markdown(index=False)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"# Data from {os.path.basename(input_path)}\n\n")
            f.write(markdown_table)
            
        print(f"Successfully converted {input_path} to {output_path}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python excel_to_markdown.py <input_xlsx> <output_md>")
    else:
        excel_to_markdown(sys.argv[1], sys.argv[2])
