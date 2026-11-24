import shlex

START = "# >>> claude-geo-guard >>>"
END = "# <<< claude-geo-guard <<<"


def block(bin_dir):
    return f'\n{START}\nexport PATH={shlex.quote(str(bin_dir))}:"$PATH"\n{END}\n'


def add(text, managed):
    if START in text:
        if text.count(managed) != 1:
            raise ValueError("edited PATH block; reconcile shell config first")
        return text
    return text + managed


def remove(text, managed):
    if START in text and text.count(managed) != 1:
        raise ValueError("edited PATH block; preserving shell config")
    return text.replace(managed, "", 1)
