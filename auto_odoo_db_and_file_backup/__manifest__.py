# -*- coding: utf-8 -*-
{
    'name': "Auto Odoo DB And File Backup",

    'summary': 'Automated DB And File backups',

    'description': """
        Automated DB And File backups
    """,
    'category': 'Administration',
    'author': "Icon Technology",
    'website': "http://www.icontechnology.in",
    'support':  'info@icontechnology.in',
    'maintainer': 'Icon Technology',
    'images': ['static/description/icon.png'],
    #odoo14
    'version': '1.1',
    # any module necessary for this one to work correctly
    'depends': ['base','google_drive','mail','sms'],
    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/auto_backup_mail_templates.xml',
        'data/data.xml',
        'views/views.xml',
        
    ],
    'installable': True,
    'application': True,
}
