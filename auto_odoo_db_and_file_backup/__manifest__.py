# -*- coding: utf-8 -*-
#odoo14
{
    'name': "Automatic Backup (Google Drive, Dropbox, FTP, SFTP, Local)",
	'category': 'Extra Tool',
	'version': '1.0', 
	
    'summary': 'Automatic Backup -(Google Drive, Dropbox, FTP, SFTP, Local)',
    'description': "Automatic Backup -(Google Drive, Dropbox, FTP, SFTP, Local)",
	'license': 'OPL-1',
    'price': 25.99,
	'currency': 'EUR',
	
	'author': "Icon Technology",
    'website': "https://icontechnology.co.in",
    'support':  'team@icontechnology.in',
    'maintainer': 'Icon Technology',
	
	'images': ['static/description/auto-backup-odoo-v14.jpg',
	'static/description/auto-backup_screenshot.gif'],
	
    # any module necessary for this one to work correctly
    'depends': ['base','google_drive','mail','sms'],
    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/auto_backup_mail_templates.xml',
        'data/data.xml',
    ],
    'installable': True,
    'application': True,
}
