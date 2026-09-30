import os
import sys
import warnings

# The upstream parser regex strings are not raw strings; on Python 3.12 they
# emit SyntaxWarning at import. Silence them so the suite output stays clean.
warnings.filterwarnings("ignore", category=SyntaxWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Make the repository root importable regardless of the pytest invocation dir.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
