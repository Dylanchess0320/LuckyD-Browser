f = r"C:\Users\dylan\OneDrive\Desktop\movie-recommender\android-app\app\src\main\java\com\dylan\whattowatch\feed\FeedAdapter.kt"
with open(f, encoding="utf-8") as fh:
    content = fh.read()
idx = content.find("binding.meta.text")
start = content.rfind("binding.title.text", 0, idx)
end = content.find("binding.genres.text", idx)
print(f"start={start} end={end}")
print("Old:" + repr(content[start : end + len("binding.genres.text")]))
