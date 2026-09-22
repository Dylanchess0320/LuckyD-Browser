import codecs

f = r"C:\\Users\\dylan\\OneDrive\\Desktop\\movie-recommender\\android-app\\app\\src\\main\\java\\com\\dylan\\whattowatch\\feed\\FeedAdapter.kt"
with codecs.open(f, "r", "utf-8") as fh:
    lines = fh.readlines()

# Fix line 91 indentation
lines[90] = "            binding.backdrop.load(t.backdropUrl()) {\n"

content = "".join(lines)

# Fix match text
content = content.replace('text = "? \\${mp}% match"', 'text = "✨ \\${mp}% match"')

with codecs.open(f, "w", "utf-8") as fh:
    fh.write(content)
print("Done")
