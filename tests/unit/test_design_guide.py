"""The links of docs/design-guide.md that point at a line of the code (`path#Ln`) must land on the line that holds what the
link names: the line numbers move whenever the code above them changes, and a reader who follows the link to see where the
tenants are kept apart has to arrive at that code."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "design-guide.md"
LINK = re.compile(r"\[([^\]]+)\]\(\.\./([^)#]+)#L(\d+)\)")


def line_links():
    return LINK.findall(GUIDE.read_text(encoding="utf-8"))


def test_the_guide_has_the_links_of_the_three_places_that_keep_the_tenants_apart():
    targets = {(path, name) for label, path, _ in line_links() for name in re.findall(r"`([^`]+)`", label)}
    for path, name in (("services/api/app/rules.py", "authorize_tenant"), ("services/api/app/api.py", "_tenant"),
                       ("services/api/app/video_jwt.py", "issue"), ("infra/mediamtx/mediamtx.yml", "mediamtx.yml"),
                       ("services/api/app/dynsec.py", "CAMERA_STATE_TOPIC_ACL")):
        assert (path, name) in targets, (path, name)


def test_every_line_link_lands_on_the_line_of_what_it_names():
    links = line_links()
    assert len(links) >= 8
    for label, path, n in links:
        lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
        assert 1 <= int(n) <= len(lines), (path, n)
        line = lines[int(n) - 1]
        name = re.findall(r"`([^`]+)`", label)[-1]
        if name == pathlib.Path(path).name:      # a link to a setting of a file: the line that turns the check on
            assert line.strip() and not line.lstrip().startswith("#"), (path, n, line)
        else:
            assert re.search(rf"\b{re.escape(name)}\b", line), (label, path, n, line)
