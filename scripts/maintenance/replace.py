f = "C:\\Users\\dylan\\OneDrive\\Desktop\\movie-recommender\\android-app\\app\\src\\main\\java\\com\\dylan\\whattowatch\\feed\\FeedAdapter.kt"
with open(f, encoding="utf-8") as fh:
    c = fh.read()
old = '            binding.meta.text = "   \u2605    " +\n                if (t.mediaType == "movie") "Movie" else "TV Show"'
new = (
    '            binding.ratingText.text = if (t.rating > 0) "%.1f".format(t.rating) else "\u2013"\n'
    + "            binding.meta.text = buildString {\n"
    + '                if (t.votes > 0) append(compactCount(t.votes)).append(" votes")\n'
    + '                else append("New")\n'
    + '                append("  \u00b7  ").append(t.year)\n'
    + '                append("  \u00b7  ").append(if (t.mediaType == "movie") "Movie" else "TV")\n'
    + "            }"
)
if old in c:
    c = c.replace(old, new)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(c)
    print("Replaced")
else:
    print("Not found")
    idx = c.find("binding.meta.text")
    if idx >= 0:
        print(repr(c[idx : idx + 200]))
