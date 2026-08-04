import sys

f = r"C:\Users\dylan\OneDrive\Desktop\movie-recommender\android-app\app\src\main\java\com\dylan\whattowatch\feed\FeedAdapter.kt"
with open(f, encoding="utf-8") as fh:
    content = fh.read()

# Find section start
idx = content.find("binding.meta.text")
start = content.rfind("binding.title.text = t.title", 0, idx)
end = content.find("binding.genres.text", idx) + len("binding.genres.text")
if start < 0 or end < 0:
    print("Could not find boundaries")
    sys.exit(1)

old_section = content[start:end]

new_lines = [
    '            binding.ratingText.text = if (t.rating, 0) "%.1f".format(t.rating) else "\u2013"',
    "            binding.meta.text = buildString {",
    '                if (t.votes > 0) append(compactCount(t.votes)).append(" votes")',
    '                else append("New")',
    '                append("  \u00b7  ").append(t.year)',
    '                append("  \u00b7  ").append(if (t.mediaType == "movie") "Movie" else "TV")',
    "            }",
    "            binding.genres.text",
]
new_section = "\n".join(new_lines)

if old_section not in content:
    print("old section not found in content")
    sys.exit(1)

content = content.replace(old_section, new_section)
with open(f, "w", encoding="utf-8") as fh:
    fh.write(content)
print("Replaced successfully")
