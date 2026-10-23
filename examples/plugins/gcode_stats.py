"""Adds a statistics comment to the end of every G-code file and logs exports."""

PLUGIN = {
    "name": "G-code statistics",
    "version": "1.0",
    "author": "iyiCAM",
    "description": "Appends line count and toolpath summary comments to generated G-code",
}


def register(api):
    def add_stats(text, job, post):
        lines = text.splitlines()
        eol = "\r\n" if "\r\n" in text else "\n"
        names = ", ".join(tp.name for tp in job.toolpaths) if job is not None else ""
        footer = [f"(iyiCAM stats: {len(lines)} lines)", f"(toolpaths: {names})"]
        if not post.options.get("comments", True):
            return text
        return text + eol.join(footer) + eol

    api.add_gcode_filter(add_stats)
