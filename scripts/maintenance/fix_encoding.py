f = r"C:\Users\dylan\OneDrive\Desktop\movie-recommender\android-app\app\src\main\java\com\dylan\whattowatch\MainActivity.kt"
with open(f, "rb") as fh:
    content = fh.read().decode("utf-8")

# Fix the encoding issues
content = content.replace(
    'sheet.detailMatch.text = "? \\${mp}% match"',
    'sheet.detailMatch.text = "✨ \\${mp}% match"',
)
content = content.replace(
    'append(title.year).append("  ?  ")', 'append(title.year).append("  ·  ")'
)
content = content.replace('append("  ?  ? ")', 'append("  ·  ★ ")')
content = content.replace('joinToString("  ?  ")', 'joinToString("  ·  ")')

with open(f, "w", encoding="utf-8") as fh:
    fh.write(content)
print("Done")
