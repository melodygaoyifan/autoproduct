from autoproduct.diff import parse_unified_diff
from autoproduct.orchestrator.mode_router import select_mode

DOCS_ONLY = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # Title
+One more sentence.
"""

AUTH_TOUCH = """\
diff --git a/app/auth/session.py b/app/auth/session.py
--- a/app/auth/session.py
+++ b/app/auth/session.py
@@ -1,2 +1,3 @@
 import jwt
+ALGO = "none"
"""

SAFETY_REMOVAL = """\
diff --git a/app/views.py b/app/views.py
--- a/app/views.py
+++ b/app/views.py
@@ -1,3 +1,2 @@
-@login_required
 def export_users(request):
     return csv_dump(User.objects.all())
"""


def test_docs_only_routes_fast():
    assert select_mode(parse_unified_diff(DOCS_ONLY)) == "fast"


def test_high_risk_path_routes_deep():
    assert select_mode(parse_unified_diff(AUTH_TOUCH)) == "deep"


def test_safety_removal_routes_standard():
    assert select_mode(parse_unified_diff(SAFETY_REMOVAL)) == "standard"


def test_user_override_wins():
    assert select_mode(parse_unified_diff(DOCS_ONLY), "deep") == "deep"


def test_planted_fixture_routes_standard(planted_diff_text):
    assert select_mode(parse_unified_diff(planted_diff_text)) == "standard"
