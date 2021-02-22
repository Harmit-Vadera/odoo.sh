# -*- coding: utf-8 -*-


import odoo
# from odoo.tools import  config
# config = odoo.tools.config

# import atexit
# import csv # pylint: disable=deprecated-module
import logging
# import os
# import signal
# import sys
# import threading
# import traceback
# import time
# 
# from psycopg2 import ProgrammingError, errorcodes
# 

# 
# __author__ = odoo.release.author
# __version__ = odoo.release.version
# 
# # Also use the `odoo` logger for the main script.
_logger = logging.getLogger('odoo')
# 
# 
# def report_configuration_custom():
#     
config = odoo.tools.config
 
print("fffffffffff-------------",config['limit_time_cpu'])
config['limit_time_cpu'] = 600
config['limit_time_real'] = 1200
config['limit_memory_hard'] = 5368706371
config['limit_memory_soft'] = 4831835734
config['workers'] = 17
print("c-c-c-c-c-c-c--c---------",config['limit_time_cpu'])
_logger.info("Updating custom confuguration____________________")
