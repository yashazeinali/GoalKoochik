# Place in PythonAnywhere's generated WSGI file; replace YOUR_USERNAME.
import sys
project='/home/YOUR_USERNAME/GoalKoochik'
if project not in sys.path:sys.path.insert(0,project)
from hosted import application
