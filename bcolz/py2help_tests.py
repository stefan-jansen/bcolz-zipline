import sys

if sys.version_info.minor >= 3:
    from unittest.mock import Mock
else:
    raise ImportError('Python 3.3 or greater is required')
Mock = Mock
