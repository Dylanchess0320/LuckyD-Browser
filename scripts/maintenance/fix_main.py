import codecs

f = r"C:\\Users\\dylan\\OneDrive\\Desktop\\movie-recommender\\android-app\\app\\src\\main\\java\\com\\dylan\\whattowatch\\MainActivity.kt"
with codecs.open(f, "r", "utf-8-sig") as fh:
    c = fh.read()
