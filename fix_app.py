import re

with open('app.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Fix PyArrow float/string mix issue by appending .fillna("") to .dt.strftime
code = re.sub(r'(\.dt\.strftime\([^)]+\))', r'\1.fillna("")', code)

# 2. Replace use_container_width=True with width="stretch" ONLY in st.dataframe calls.
# We can do this safely by splitting the code by "st.dataframe" and replacing in the chunks,
# except the first chunk.
chunks = code.split('st.dataframe')
for i in range(1, len(chunks)):
    # Find the end of the st.dataframe call (simple heuristic: look for the next few lines)
    # Actually, we can just replace the first occurrence of use_container_width=True in this chunk
    chunks[i] = chunks[i].replace('use_container_width=True', 'width="stretch"', 1)

code = 'st.dataframe'.join(chunks)

# Just in case there was use_container_width=False
code = code.replace('use_container_width=False', 'width="content"')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("app.py fixed")
