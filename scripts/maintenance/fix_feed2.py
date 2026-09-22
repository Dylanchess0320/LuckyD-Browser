import codecs

f = r"C:\\Users\\dylan\\OneDrive\\Desktop\\movie-recommender\\android-app\\app\\src\\main\\java\\com\\dylan\\whattowatch\\feed\\FeedAdapter.kt"
with codecs.open(f, "r", "utf-8") as fh:
    content = fh.read()

# Fix indentation - add 4 spaces before binding.backdrop.load
content = content.replace(
    "binding.backdrop.load(t.backdropUrl()) {",
    "    binding.backdrop.load(t.backdropUrl()) {",
)

# Fix indentation - add 12 spaces before binding.meta.text
content = content.replace(
    "binding.meta.text = buildString {", "            binding.meta.text = buildString {"
)

with codecs.open(f, "w", "utf-8") as fh:
    fh.write(content)
print("Done")
