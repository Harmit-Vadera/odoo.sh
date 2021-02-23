# -*- coding: utf-8 -*-

import os
import datetime
import time
import shutil
import json
import tempfile

from odoo import models, fields, api,exceptions, _
from odoo.exceptions import Warning, AccessDenied, RedirectWarning, UserError
import odoo
import pytz
try:
    from xmlrpc import client as xmlrpclib
except ImportError:
    import xmlrpclib
    
import logging
_logger = logging.getLogger(__name__)
import socket
from odoo.addons.google_drive.models.google_drive import GoogleDrive
import requests
from odoo.addons.google_account.models.google_service import GOOGLE_TOKEN_ENDPOINT, TIMEOUT
import sys, subprocess
py_v = "python%s.%s" % (sys.version_info.major,sys.version_info.minor)
# try:
#     import dropbox
# except ImportError:
#     print('\n There was no such module named -dropbox- installed')
#     print('xxxxxxxxxxxxxxxx installing dropbox xxxxxxxxxxxxxx')
#     subprocess.check_call([py_v, "-m", "pip", "install","--user", "dropbox"])
#     import dropbox
# from dropbox.files import WriteMode
# from dropbox.exceptions import ApiError, AuthError    
# try:
#     import ftplib
# except ImportError:
#     print('\n There was no such module named -ftplib- installed')
#     print('xxxxxxxxxxxxxxxx installing ftplib xxxxxxxxxxxxxx')
#     subprocess.check_call([py_v, "-m", "pip", "install","--user", "ftplib"])
#     import ftplib
# from dateutil.relativedelta import relativedelta
# try:
#     import pysftp
# except ImportError:
#     print('\n There was no such module named -pysftp- installed')
#     print('xxxxxxxxxxxxxxxx installing pysftp xxxxxxxxxxxxxx')
#     subprocess.check_call([py_v, "-m", "pip", "install","--user", "pysftp"])
#     import pysftp
from paramiko.ssh_exception import SSHException
import base64
import io

_intervalTypes = {
    'days': lambda interval: relativedelta(days=interval),
    'hours': lambda interval: relativedelta(hours=interval),
    'weeks': lambda interval: relativedelta(days=7*interval),
    'months': lambda interval: relativedelta(months=interval),
    'minutes': lambda interval: relativedelta(minutes=interval),
}

def execute(connector, method, *args):
    res = False
    try:
        res = getattr(connector, method)(*args)
    except socket.error as error:
        _logger.critical('Error while executing the method "execute". Error: ' + str(error))
        raise error
    return res

class ir_cron(models.Model):
    _inherit = "ir.cron"
    
    @classmethod
    def _process_job(cls, job_cr, job, cron_cr):
        """ Run a given job taking care of the repetition.

        :param job_cr: cursor to use to execute the job, safe to commit/rollback
        :param job: job to be run (as a dictionary).
        :param cron_cr: cursor holding lock on the cron job row, to use to update the next exec date,
            must not be committed/rolled back!
        """
        try:
            with api.Environment.manage():
                cron = api.Environment(job_cr, job['user_id'], {
                    'lastcall': fields.Datetime.from_string(job['lastcall'])
                })[cls._name]
                # Use the user's timezone to compare and compute datetimes,
                # otherwise unexpected results may appear. For instance, adding
                # 1 month in UTC to July 1st at midnight in GMT+2 gives July 30
                # instead of August 1st!
                now = fields.Datetime.context_timestamp(cron, datetime.datetime.now())
                nextcall = fields.Datetime.context_timestamp(cron, fields.Datetime.from_string(job['nextcall']))
                numbercall = job['numbercall']

                ok = False
                while nextcall < now and numbercall:
                    if numbercall > 0:
                        numbercall -= 1
                    if not ok or job['doall']:
                        cron._callback(job['cron_name'], job['ir_actions_server_id'], job['id'])
                    if numbercall:
                        nextcall += _intervalTypes[job['interval_type']](job['interval_number'])
                    ok = True
                addsql = ''
                if not numbercall:
                    addsql = ', active=False'
                cron_cr.execute("UPDATE ir_cron SET nextcall=%s, numbercall=%s, lastcall=%s"+addsql+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    numbercall,
                    fields.Datetime.to_string(now.astimezone(pytz.UTC)),
                    job['id']
                ))
                #custom code
                if job['id'] == cron.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox').id:
                    bid = cron.env.ref('auto_odoo_db_and_file_backup.rule_upload_backup_to_dropbox').id
                    cron_cr.execute("UPDATE database_backup SET next_exec_dt=%s"+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    bid ))
                if job['id'] == cron.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler').id:
                    bid = cron.env.ref('auto_odoo_db_and_file_backup.rule_upload_backup_to_folder').id
                    cron_cr.execute("UPDATE database_backup SET next_exec_dt=%s"+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    bid ))
                if job['id'] == cron.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive').id:
                    bid = cron.env.ref('auto_odoo_db_and_file_backup.rule_upload_backup_to_drive').id
                    cron_cr.execute("UPDATE database_backup SET next_exec_dt=%s"+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    bid ))
                if job['id'] == cron.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp').id:
                    bid = cron.env.ref('auto_odoo_db_and_file_backup.rule_upload_backup_to_ftp').id
                    cron_cr.execute("UPDATE database_backup SET next_exec_dt=%s"+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    bid ))
                if job['id'] == cron.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp').id:
                    bid = cron.env.ref('auto_odoo_db_and_file_backup.rule_upload_backup_to_sftp').id
                    cron_cr.execute("UPDATE database_backup SET next_exec_dt=%s"+" WHERE id=%s",(
                    fields.Datetime.to_string(nextcall.astimezone(pytz.UTC)),
                    bid ))   
                cron.flush()
                cron.invalidate_cache()

        finally:
            job_cr.commit()
            cron_cr.commit()

class AutoDatabaseBackupStatus(models.Model):
    _name = 'auto.database.backup.status'
    _description = 'Auto Database Backup Status'
    
    name = fields.Char("Status")
    date = fields.Datetime("Date")
        
class AutoDatabaseBackup(models.Model):
    _name = 'auto.database.backup'
    _description = 'Auto Database Backup configuration'
    
    bkup_email = fields.Char("Successful Backup Notification Email")
    bkup_fail_email = fields.Char("Failed Backup Notification Email")
    autoremove = fields.Boolean('Auto. Remove Backups',
                                help='If you check this option you can choose to automatically remove the backup '
                                     'after xx days')
    days_to_keep = fields.Integer('Remove after x days',
                                  help="Choose after how many days the backup should be deleted. For example:\n"
                                       "If you fill in 5 the backups will be removed after 5 days.",
                                  )
    name = fields.Char("Filename")
    bkpu_rules = fields.One2many('database.backup','backup_id',"Auto Database Backup Rules")
    
class DatabaseBackup(models.Model):
    _name = 'database.backup'
    _description = 'Auto Database Backup Rules'
    
    def _get_abs_file_path(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__))).split('/')
        p1 = path[: len(path) - 3]
        p2 = "/".join(p1)
        dirlist = [(os.path.join(p2,filename),os.path.join(p2,filename)) for filename in os.listdir(p2) if os.path.isdir(os.path.join(p2,filename))]
        return dirlist   
    
    def _get_abs_file_path2(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__))).split('/')
        p1 = path[: len(path) - 3]
        p2 = "/".join(p1)
        dirlist = [(os.path.join(p2,filename),os.path.join(p2,filename)) for filename in os.listdir(p2) if os.path.isdir(os.path.join(p2,filename))]
        return dirlist 
    
    # Columns for local server configuration
    backup_id = fields.Many2one("auto.database.backup","Auto Backup")
    is_active = fields.Boolean("Active",)
    interval_number = fields.Integer("Interval Number",default=1,)
    interval_type = fields.Selection([('minutes', 'Minutes'),
                                      ('hours', 'Hours'),
                                      ('days', 'Days'),
                                      ('weeks', 'Weeks'),
                                      ('months', 'Months')], string='Interval Unit', default='days')
    backup_type = fields.Selection([('zip', 'Zip'), ('dump', 'Dump')], 'Backup Type', required=True, default='zip')
    backup_destination = fields.Selection([('folder', 'Folder'), ('g_drive', 'Google Drive'),
                                           ('dropbox', 'Dropbox'),('ftp', 'FTP'),('sftp', 'SFTP')], 'Backup Destination', readonly=True,default='folder')
    next_exec_dt = fields.Datetime("Next Excecution Date",default=fields.Datetime.now,required=True,)
    backup = fields.Selection([('db_only', 'Database Only'), ('db_and_files', 'Database and Files')], 'Backup', default='db_only')
    files_path = fields.Selection(selection=_get_abs_file_path,string='Files Path', help="Mention files path for the files, you want to take backup.")
    folder = fields.Selection(selection=_get_abs_file_path2,string='Backup Directory', help='Absolute path for storing the backups')
    foldername = fields.Char("Foldername",help='Foldername for storing the backups',default="Backups")
    
    #fields for Google Drive uploads
    google_drive_uri = fields.Char(compute='_compute_drive_uri', string='URI', help="The URL to generate the authorization code from Google")
    google_drive_authorization_code = fields.Char(string='Google Authorization Code')
    google_drive_refresh_token = fields.Text(string='google Drive Refresh Token',)
    google_drive_authorization_code_old = fields.Char(string='Old Google Authorization Code')
    
    ###dropbox fields
    d_app_key = fields.Char("App key")
    d_app_secret = fields.Char("App secret")
    dropbox_uri = fields.Char(compute='_compute_dropbox_uri', string='Dropbox URI', help="The URL to generate the authorization code from Dropbox")
    dropbox_authorization_code = fields.Char(string='Dropbox Authorization Code')
    dropbox_token = fields.Text(string='Access Token',)
    dropbox_authorization_code_old = fields.Char(string='Old Dropbox Authorization Code')
    
    ###FTP fields
    ftp_address = fields.Char('FTP Address',
                            help='The IP address from your remote server. For example 192.168.0.1')
    ftp_port = fields.Integer('FTP Port', help='The port on the FTP server that accepts SSH/SFTP calls.')
    ftp_usrnm = fields.Char('FTP Username',
                            help='The username where the FTP connection should be made with. This is the user on the '
                                 'external server.')
    ftp_pwd = fields.Char('FTP Password',
                                help='The password from the user where the FTP connection should be made with. This '
                                     'is the password from the user on the external server.')
    ftp_path = fields.Char('FTP Path',
                            help='The location to the folder where the dumps should be written to. For example '
                                 '/odoo/backups/.\nFiles will then be written to /odoo/backups/ on your remote server.')
    ###SFTP fields
    sftp_host = fields.Char('SFTP Host',
                            help='The IP address from your remote server. For example 192.168.0.1')
    sftp_user = fields.Char('SFTP User',
                            help='The username where the SFTP connection should be made with. This is the user on the '
                                 'external server.')
    sftp_keyfilepath = fields.Char("SFTP Key File Path(Use .pem File)",help='Add file path where key file for SFTP connection is present.')
    sftp_file_path = fields.Char('SFTP Path',
                            help='The location to the folder where the dumps should be written to. For example '
                                 '/odoo/backups/.\nFiles will then be written to /odoo/backups/ on your remote server.')
    upload_file = fields.Binary(string="Upload File")
    file_name = fields.Char(string="File Name")
    
    @api.onchange('upload_file')
    def onchange_upload_file(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__))).split('/')
        p1 = path[: len(path) - 3]
        p2 = "/".join(p1)
        try:
            path1 = p2 + "/"+self.file_name
            if not os.path.exists(path1):
                with open(path1, 'w') as fp: 
                    pass
            
            File = base64.b64decode(self.upload_file)
            file_string = File.decode('utf-8')
            file1 = open(path1, 'w+')
            file1.write(file_string)
            file1.close()
            self.sftp_keyfilepath = path1
        except Exception as e:
            msg = e
            if "Permission denied" in str(e):
                msg = "Please give write permission to Directory: %s" % (p2,)
            raise UserError(_(msg))
        
    def change_nextcall_datetime(self,rec):
        unit = rec.interval_number
        now = datetime.datetime.now()                   
        if rec.interval_type == 'days':
            dd = now + datetime.timedelta(days=unit)
            rec.write({'next_exec_dt' : dd})
        elif rec.interval_type == 'weeks':
            wunit = 7 * unit
            wd = now + datetime.timedelta(days=wunit)
            rec.write({'next_exec_dt' : wd})
        elif rec.interval_type == 'months':
            md = now + relativedelta(months=+unit)
            rec.write({'next_exec_dt' : md})
        elif rec.interval_type == 'hours':
            hd = now + datetime.timedelta(hours=unit)
            rec.write({'next_exec_dt' : hd})
        else:
            mind = now + datetime.timedelta(minutes=unit)
            rec.write({'next_exec_dt' : mind})

                   
    def write(self, vals):
        result = super(DatabaseBackup, self).write(vals)
        cr = self._cr
        if "is_active" in vals:
            if self.backup_destination == 'folder':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'active' : vals.get('is_active')})
            elif self.backup_destination == 'g_drive':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'active' : vals.get('is_active')})
            elif self.backup_destination == 'dropbox':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'active' : vals.get('is_active')})
            elif self.backup_destination == 'ftp':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'active' : vals.get('is_active')})
            else:
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'active' : vals.get('is_active')})
        if "interval_number" in vals:
            if self.backup_destination == 'folder':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_number' : vals.get('interval_number')})
            elif self.backup_destination == 'g_drive':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_number' : vals.get('interval_number')})
            elif self.backup_destination == 'dropbox':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_number' : vals.get('interval_number')})
            elif  self.backup_destination == 'ftp':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_number' : vals.get('interval_number')})
            else:
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_number' : vals.get('interval_number')})
        if "interval_type" in vals:
            if self.backup_destination == 'folder':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_type' : vals.get('interval_type')})
            elif self.backup_destination == 'g_drive':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_type' : vals.get('interval_type')})
            elif self.backup_destination == 'dropbox':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_type' : vals.get('interval_type')})
            elif self.backup_destination == 'ftp':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_type' : vals.get('interval_type')})
            else:
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                IrCron.write({'interval_type' : vals.get('interval_type')})
        if "next_exec_dt" in vals:
            if self.backup_destination == 'folder':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                cr.execute("UPDATE ir_cron SET nextcall=%s WHERE id=%s",(
                vals.get('next_exec_dt'),
                cron_id.id
            ))
            elif self.backup_destination == 'g_drive':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                cr.execute("UPDATE ir_cron SET nextcall=%s WHERE id=%s",(
                vals.get('next_exec_dt'),
                cron_id.id
            ))
            elif self.backup_destination == 'dropbox':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                cr.execute("UPDATE ir_cron SET nextcall=%s WHERE id=%s",(
                vals.get('next_exec_dt'),
                cron_id.id
            ))
            elif self.backup_destination == 'ftp':
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                cr.execute("UPDATE ir_cron SET nextcall=%s WHERE id=%s",(
                vals.get('next_exec_dt'),
                cron_id.id
            ))
            else:
                cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
                IrCron = self.env['ir.cron'].browse(cron_id.id)
                cr.execute("UPDATE ir_cron SET nextcall=%s WHERE id=%s",(
                vals.get('next_exec_dt'),
                cron_id.id
            ))
        return result
    
    def trigger_direct(self):
        backup_destination = self.env.context.get('backup_destination')
        actid = self.env.context.get('id')
        rec = self.env['database.backup'].browse(actid)
        if backup_destination == 'folder':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'g_drive':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'dropbox':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'ftp':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        else:
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        return True
        
    def test_sftp_connection(self, context=None): 
        self.ensure_one()
                                                                                                                                                             
        # Check if there is a success or fail and write messages
        message_title = ""
        message_content = ""
        error = ""
        has_failed = False

        for rec in self:
            ip_host = rec.sftp_host
            user = rec.sftp_user
            key_path = rec.sftp_keyfilepath

            # Connect with external server over SFTP, so we know sure that everything works.
            try:
                cnopts = pysftp.CnOpts()
                cnopts.hostkeys = None
                with pysftp.Connection(host=ip_host, username=user,
                                       private_key=key_path, cnopts=cnopts) as sftp:
                    message_title = _("Connection Test Succeeded!\nEverything seems properly set up for SFTP back-ups!")
            except SSHException as ssh_err:
                _logger.critical('There was a problem connecting to the remote sftp: ' + str(ssh_err.args[0]))
                error += str(ssh_err)
                has_failed = True
                message_title = _("Connection Test Failed!")
                message_content += _("Here is what we got instead:\n")

        if has_failed:
            raise Warning(message_title + '\n\n' + message_content + "%s" % str(error))
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': message_title,
                    'type': 'success',
                    'sticky': False,
                }
            }
           
    def test_ftp_connection(self, context=None):
        self.ensure_one()
                                                                                                                                                             
        # Check if there is a success or fail and write messages
        message_title = ""
        message_content = ""
        error = ""
        has_failed = False

        for rec in self:
            ip_host = rec.ftp_address
            port_host = rec.ftp_port
            username_login = rec.ftp_usrnm
            password_login = rec.ftp_pwd

            # Connect with external server over SFTP, so we know sure that everything works.
            try:
                server = ftplib.FTP()
                server.connect(ip_host, port_host)
                server.login(username_login,password_login)
                message_title = _("Connection Test Succeeded!\nEverything seems properly set up for FTP back-ups!")
            except Exception as e:
                _logger.critical('There was a problem connecting to the remote ftp: ' + str(e))
                error += str(e)
                has_failed = True
                message_title = _("Connection Test Failed!")
                if len(rec.ftp_address) < 8:
                    message_content += "\nYour IP address seems to be too short.\n"
                message_content += _("Here is what we got instead:\n")
            finally:
                if server:
                    server.close()

        if has_failed:
            raise UserError(message_title + '\n\n' + message_content + "%s" % str(error))
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': message_title,
                    'type': 'success',
                    'sticky': False,
                }
            }
        
    
    @api.depends('d_app_key')
    def _compute_dropbox_uri(self):
        if self.d_app_key:
            self.dropbox_uri = "https://www.dropbox.com/oauth2/authorize?response_type=code&client_id=%s" % (self.d_app_key,)
        else:
            self.dropbox_uri = ""
            
            
    @api.onchange('google_drive_authorization_code')     
    def _action_setup_token(self):
        for rec in self:
            if rec.google_drive_authorization_code:
                if not rec.google_drive_refresh_token:
                    authorization_code = rec.google_drive_authorization_code
                    refresh_token = (
                        self.env['google.service'].generate_refresh_token('drive', authorization_code)
                        if authorization_code else False
                    )
                    self.write({'google_drive_authorization_code_old' : authorization_code,})
                    rec.google_drive_refresh_token = refresh_token
                else:
                    if rec.google_drive_authorization_code != rec.google_drive_authorization_code_old:
                        authorization_code = rec.google_drive_authorization_code
                        refresh_token = (
                            self.env['google.service'].generate_refresh_token('drive', authorization_code)
                            if authorization_code else False
                        )
                        self.write({'google_drive_authorization_code_old' : authorization_code,})
                        rec.google_drive_refresh_token = refresh_token
                    else:
                        rec.google_drive_refresh_token = rec.google_drive_refresh_token
            else:
                rec.google_drive_refresh_token = rec.google_drive_refresh_token
    
    
    @api.onchange('dropbox_authorization_code','d_app_key','d_app_secret')
    def action_setup_dropbox_token(self):
        for rec in self:
            if rec.d_app_key and rec.d_app_secret and rec.dropbox_authorization_code:
                if not rec.dropbox_token:
                    try:
                        token_url = "https://api.dropbox.com/oauth2/token"
                        params = {
                            "code": rec.dropbox_authorization_code,
                            "grant_type": "authorization_code",
                            "client_id": rec.d_app_key,
                            "client_secret": rec.d_app_secret
                        }
                        r = requests.post(token_url, data=params)
                        response = json.loads(r.text) 
                        self.write({'dropbox_authorization_code_old' : rec.dropbox_authorization_code})
                        rec.dropbox_token = response.get('access_token') 
                    except Exception as e:
                        raise Warning(e)
                        _logger.debug(e)
                        exit(1)
                else:
                    if rec.dropbox_authorization_code != rec.dropbox_authorization_code_old:
                        try:
                            token_url = "https://api.dropbox.com/oauth2/token"
                            params = {
                                "code": rec.dropbox_authorization_code,
                                "grant_type": "authorization_code",
                                "client_id": rec.d_app_key,
                                "client_secret": rec.d_app_secret
                            }
                            r = requests.post(token_url, data=params)
                            response = json.loads(r.text) 
                            self.write({'dropbox_authorization_code_old' : rec.dropbox_authorization_code})
                            self.dropbox_token = response.get('access_token') 
                        except Exception as e:
                            raise Warning(e)
                            _logger.debug(e)
                            exit(1)
                    else:
                        rec.dropbox_token = rec.dropbox_token
            else:
                rec.dropbox_token = rec.dropbox_token

#     @api.model
    def get_access_token(self,rec,scope=None):
        Config = self.env['ir.config_parameter'].sudo()
        google_drive_refresh_token = rec.google_drive_refresh_token 
        user_is_admin = self.env.is_admin()
        if not google_drive_refresh_token:
            if user_is_admin:
                dummy, action_id = self.env['ir.model.data'].get_object_reference('base_setup', 'action_general_configuration')
                msg = _("There is no refresh code set for Google Drive. You can set it up from the configuration panel.")
                raise RedirectWarning(msg,)
            else:
                raise UserError(_("Google Drive is not yet configured. Please contact your administrator."))
        google_drive_client_id = Config.get_param('google_drive_client_id')
        google_drive_client_secret = Config.get_param('google_drive_client_secret')
        #For Getting New Access Token With help of old Refresh Token
        data = {
            'client_id': google_drive_client_id,
            'refresh_token': google_drive_refresh_token,
            'client_secret': google_drive_client_secret,
            'grant_type': "refresh_token",
            'scope': scope or 'https://www.googleapis.com/auth/drive'
        }
        headers = {"Content-type": "application/x-www-form-urlencoded; charset=utf-8"}
        try:
            req = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data, headers=headers, timeout=300)
            req.raise_for_status()
        except requests.HTTPError:
            if user_is_admin:
                dummy, action_id = self.env['ir.model.data'].get_object_reference('base_setup', 'action_autobackup')
                msg = _("Something went wrong during the token generation. Please request again an authorization code .")
                raise RedirectWarning(msg, action_id, _('Go to the Configure Auto DB Backups'))
            else:
                raise UserError(_("Google Drive is not yet configured. Please contact your administrator."))
        return req.json().get('access_token')
            
    
    @api.depends('google_drive_authorization_code')
    def _compute_drive_uri(self):
        google_drive_uri = self.env['google.service']._get_google_token_uri('drive', scope=self.env['google.drive.config'].get_google_scope())
        for config in self:
            config.google_drive_uri = google_drive_uri
     
    def send_success_mail_notificaton(self,rec,bkp_file,bkp_folder):
        email_to = rec.backup_id.bkup_email
        BackupType = dict(self._fields['backup_type'].selection)
        BackupDest = dict(self._fields['backup_destination'].selection)
        if rec.backup_destination == 'folder':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_local_upload')
            folder = rec.folder
            if rec.folder.endswith('/'):
                folder = rec.folder.strip('/')
            submsg1 = folder + "/" + rec.foldername
            submsg2 = ""
            subject = "Folder Upload Successful"
        if rec.backup_destination == 'g_drive':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_google_drive_upload')
            
            submsg1 = "<a href='https://drive.google.com/drive/my-drive'>https://drive.google.com/drive/my-drive</a>"
            submsg2 = ""
            subject = "Google Drive Upload Successful"
        if rec.backup_destination == 'dropbox':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_dropbox_upload')
            submsg1 = "<a href='https://www.dropbox.com/home?preview=%s'>https://www.dropbox.com/home?preview=%s</a>"%(bkp_file,bkp_file)
            submsg2 = "<a href='https://www.dropbox.com/home?preview=%s'>https://www.dropbox.com/home?preview=%s</a>"%(bkp_folder,bkp_folder)
            subject = "Dropbox Upload Successful"
        if rec.backup_destination == 'ftp':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_ftp_upload')
            submsg1 = rec.ftp_path
            submsg2 = ""
            subject = "FTP Upload Successful"
        if rec.backup_destination == 'sftp':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_sftp_upload')
            submsg1 = rec.sftp_file_path
            submsg2 = ""
            subject = "SFTP Upload Successful"
            
        if rec.backup == 'db_only':
            msg = "<h3>Backup Successfully Created!</h3>" \
                              "Please see below details. <br/> <br/> " \
                              "<p>Backup : Database Only </p>" \
                              "<p>Backup Type : %s" % (str(BackupType.get(rec.backup_type))) + "</p>" \
                              "<p>Backup Destination : %s" % (str(BackupDest.get(rec.backup_destination))) + "</p>" \
                              "<p>Backup Directory : %s" % (str(submsg1)) + "</p>" \
                              "<p>Filename : %s" % (str(bkp_file)) + "</p>" 
        else:
            msg = "<h3>Backup Successfully Created!</h3>" \
                              "Please see below details. <br/> <br/> " \
                              "<p>Backup : Database and Files</p>" \
                              "<p>Backup Type : %s" % (str(BackupType.get(rec.backup_type))) + "</p>" \
                              "<p>Backup Destination : %s" % (str(BackupDest.get(rec.backup_destination))) + "</p>" \
                              "<p>Backup Directory : %s" % (str(submsg1))+"<br/>"+(str(submsg2)) + "</p>" \
                              "<p>DB Filename : %s" % (str(bkp_file)) + "</p>"  \
                              "<p>Files : %s" % (str(bkp_folder)) + "</p>"
                              
        values = notification_template.generate_email(rec.id)
        values['email_from'] = self.env['res.users'].browse(self.env.uid).company_id.email
        values['email_to'] = email_to
        values['subject'] =  subject 
        values['body_html'] = msg
        
        send_mail = self.env['mail.mail'].create(values)
        send_mail.send()
        
        
    def send_fail_mail_notificaton(self,rec,bkp_file,bkp_folder,error):
        email_to = rec.backup_id.bkup_fail_email
                            
        if rec.backup_destination == 'folder':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_local_upload')
            subject = "Folder Upload Failed"
        if rec.backup_destination == 'g_drive':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_google_drive_upload')
            
            subject = "Google Drive Upload Failed"
        if rec.backup_destination == 'dropbox':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_dropbox_upload')
            subject = "Dropbox Upload Failed"
        if rec.backup_destination == 'ftp':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_ftp_upload')
            subject = "FTP Upload Failed"
        if rec.backup_destination == 'sftp':
            notification_template = self.env['ir.model.data'].sudo().get_object('auto_odoo_db_and_file_backup',
                                                                            'email_sftp_upload')
            subject = "SFTP Upload Failed"
            
        if rec.backup == 'db_only':
            msg = "<h3>Backup Upload Failed!</h3>" \
                                  "Please see below details. <br/> <br/> " \
                                  "<table style='width:100%'>" \
                                  "<tr> " \
                                  "<th align='left'>Backup</th>" \
                                  "<td>" + (str(bkp_file)) + "</td></tr>" \
                                                             "<tr> " \
                                                             "<th align='left'>Error: </th>" \
                                                             "<td>" + str(error) + "</td>" \
                                                                                  "</tr>" \
                                                  "</table>"
        else:
            msg = "<h3>Backup Upload Failed!</h3>" \
                                  "Please see below details. <br/> <br/> " \
                                  "<table style='width:100%'>" \
                                  "<tr> " \
                                  "<th align='left'>Backup</th>" \
                                  "<td>" + (str(bkp_file)) + (str(bkp_folder)) + "</td></tr>" \
                                                             "<tr> " \
                                                             "<th align='left'>Error: </th>" \
                                                             "<td>" + str(error) + "</td>" \
                                                                                  "</tr>" \
                                                  "</table>"
                                              
        values = notification_template.generate_email(rec.id)
        values['email_from'] = self.env['res.users'].browse(self.env.uid).company_id.email
        values['email_to'] = email_to
        values['subject'] = subject
        values['body_html'] = msg

        send_mail = self.env['mail.mail'].create(values)
        send_mail.send()
            
    @api.model
    def schedule_auto_db_backup(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            if rec.is_active:
                if rec.backup_destination == 'folder':
                    user_tz = pytz.timezone(self.env.context.get('tz') or self.env.user.tz)
                    date_today = pytz.utc.localize(datetime.datetime.today()).astimezone(user_tz)
                    try:
                        StatusObj = self.env['auto.database.backup.status']
                        folder = rec.folder
                        if rec.folder.endswith('/'):
                            folder = rec.folder.strip('/')
                        Folder_Path = folder + "/" + rec.foldername
                        if not os.path.isdir(Folder_Path):
                            os.makedirs(Folder_Path)
                        # Create name for dumpfile.
                        bkp_file = '%s_%s.%s' % ( self.env.cr.dbname,date_today.strftime('%Y-%m-%d_%H_%M_%S'), rec.backup_type)
                        file_path = os.path.join(Folder_Path, bkp_file)
                        bkp_folder = ""
                        # try to backup database and write it away
#                         fp = open(file_path, 'wb')
#                         self._take_dump(self.env.cr.dbname, fp, 'database.backup',rec.backup_destination, rec.backup_type)
#                         fp.close()
                        fp = open(file_path, 'wb')
                        odoo.service.db.dump_db(self.env.cr.dbname, fp, rec.backup_type)
                        fp.close()
                        if rec.backup == 'db_and_files':
                            fpath = rec.files_path.split('/')[-1]
                            bkp_folder = '%s_%s.%s' % (fpath,date_today.strftime('%Y-%m-%d_%H_%M_%S'), "zip")
                            bkp_folder_path  = os.path.join(Folder_Path,bkp_folder) 
                            with tempfile.TemporaryDirectory() as dump_dirf:
                                if os.path.exists(rec.files_path):
                                    shutil.copytree(rec.files_path, os.path.join(dump_dirf, fpath))
                                
                                odoo.tools.osutil.zip_dir(dump_dirf, bkp_folder_path, include_dir=False, fnct_sort=None) 
                            
                        _logger.info("Backup Successfully Uploaded to Local.")
                        StatusObj.create({'date': datetime.datetime.today(), 'name' : "Success"})
                        if rec.backup_id.bkup_email:
                            self.send_success_mail_notificaton(rec,bkp_file,bkp_folder) 
                
                    except Exception as error:
                        _logger.debug(
                            "Couldn't backup database %s. Bad database administrator password for server running at "
                            % (self.env.cr.dbname,))
                        _logger.debug("Exact error from the exception: " + str(error))
                        StatusObj.create({'date': datetime.datetime.today(), 'name' : "Failed (Error: %s)" % (str(error))})
                        if rec.backup_id.bkup_fail_email:
                            self.send_fail_mail_notificaton(rec,bkp_file,bkp_folder,error) 
                            
                        continue
        
                    """
                    Remove all old files (on local server) in case this is configured..
                    """
                    if rec.backup_id.autoremove:
                        
                        directory = Folder_Path
                        # Loop over all files in the directory.
                        for f in os.listdir(directory):
                            fullpath = os.path.join(directory, f)
                            # Only delete the ones which are from the current database
                            # (Makes it possible to save different databases in the same folder)
                            if self.env.cr.dbname in fullpath:
                                timestamp = os.path.getmtime(os.path.join(directory, f)) 
                                createtime = datetime.datetime.fromtimestamp(timestamp)
                                now = datetime.datetime.now()
                                delta = now.date() - createtime.date()
                                if delta.days >= rec.backup_id.days_to_keep:
                                    # Only delete files (which are .dump and .zip), no directories.
                                    if os.path.isfile(fullpath) and (".dump" in f or '.zip' in f):
                                        _logger.info("Delete local out-of-date file: " + fullpath)
                                        os.remove(fullpath)
                            if rec.backup == "db_and_files":
                                fpath = rec.files_path.split('/')[-1]
                                if fpath in fullpath:
                                    timestamp = os.path.getmtime(os.path.join(directory, f)) 
                                    createtime = datetime.datetime.fromtimestamp(timestamp)
                                    now = datetime.datetime.now()
                                    delta = now.date() - createtime.date()
                                    if delta.days >= rec.backup_id.days_to_keep:
                                        if os.path.isfile(fullpath) and ('.zip' in f):
                                            _logger.info("Delete local out-of-date file: " + fullpath)
                                            os.remove(fullpath)
                                
    def get_content_files(self,rec):
        err = ""
        user_tz = pytz.timezone(self.env.context.get('tz') or self.env.user.tz)
        date_today = pytz.utc.localize(datetime.datetime.today()).astimezone(user_tz)
        try:
            # Create name for dumpfile.
            bkp_file = '%s_%s.%s' % (self.env.cr.dbname,date_today.strftime('%Y-%m-%d_%H_%M_%S'), rec.backup_type)
            
            fd, patht = tempfile.mkstemp(bkp_file) #can use anything 
            try:
#                 self._take_dump(self.env.cr.dbname, patht, 'database.backup',rec.backup_destination, rec.backup_type)
                fp = open(patht, 'wb')
                odoo.service.db.dump_db(self.env.cr.dbname, fp, rec.backup_type)
                fp.close()
            except Exception as E:
                print("Error: ",E)
                _logger.info("::::---- Error: %s ----::::" % str(E))
            with open(patht, 'rb') as db_document:
                db_content = db_document.read()
            dbfile_content = ""
            bkp_folder = ""
            bkp_folder_path = ""
            if rec.backup == 'db_and_files':
                fpath = rec.files_path.split('/')[-1]
                bkp_folder = '%s_%s.%s' % (fpath,date_today.strftime('%Y-%m-%d_%H_%M_%S'), "zip")
                dump_dirf = tempfile.mkdtemp()
                bkp_folder_path  = os.path.join(dump_dirf,bkp_folder) 
                if os.path.exists(rec.files_path):
                    shutil.copytree(rec.files_path, os.path.join(dump_dirf, fpath))
                
                odoo.tools.osutil.zip_dir(dump_dirf, bkp_folder_path, include_dir=False, fnct_sort=None)
                with open(bkp_folder_path, 'rb') as dbfile_document:
                    dbfile_content = dbfile_document.read()
            return  bkp_file, patht, bkp_folder, bkp_folder_path, err, datetime.datetime.today(),db_content,dbfile_content  
        except Exception as error:
            _logger.debug(
                "Couldn't backup database %s. Bad database administrator password for server running" % (
                self.env.cr.dbname, ))
            _logger.debug("Exact error from the exception: " + str(error)) 
            return  "", "", "", "", error, datetime.datetime.today(),"",""
        
    @api.model
    def schedule_auto_db_backup_to_Gdrive(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            if rec.is_active:
                StatusObj = self.env['auto.database.backup.status']
                if rec.backup_destination == 'g_drive':
                    bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today,db_content, dbfile_content = self.get_content_files(rec)
                    if err == "":
                        status = 1
                        self.google_drive_upload(rec, file_path2, bkp_file, bkp_file, bkp_folder, status, date_today,db_content,dbfile_content)
                        status = 2
                        time.sleep(3)
                        if rec.backup == 'db_and_files':
                            self.google_drive_upload(rec, bkp_folder_path, bkp_folder, bkp_file, bkp_folder, status, date_today,db_content,dbfile_content)
                    else:
                        StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
    
    @api.model
    def schedule_auto_db_backup_to_dropbox(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            if rec.is_active:
                StatusObj = self.env['auto.database.backup.status']
                if rec.backup_destination == 'dropbox':
                    bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today,db_content, dbfile_content = self.get_content_files(rec)
                    if err == "":
                        status = 1
                        self.dropbox_upload(rec, file_path2, bkp_file,bkp_file,bkp_folder,status, date_today,db_content, dbfile_content)
                        time.sleep(3)
                        status = 2
                        if rec.backup == 'db_and_files':
                            self.dropbox_upload(rec, bkp_folder_path, bkp_folder,bkp_file,bkp_folder,status, date_today,db_content, dbfile_content )
                    else:
                        StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
    
    @api.model
    def schedule_auto_db_backup_to_ftp(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            if rec.is_active:
                if rec.backup_destination == 'ftp':
                    bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = self.get_content_files(rec)
                    self.ftp_upload(rec, file_path2, bkp_file, bkp_folder_path ,bkp_folder, err, date_today, db_content, dbfile_content)
    
    @api.model
    def schedule_auto_db_backup_to_sftp(self):
        conf_ids = self.search([])
        for rec in conf_ids:
            if rec.is_active:
                if rec.backup_destination == 'sftp':
                    bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today,db_content,dbfile_content = self.get_content_files(rec)
                    self.sftp_upload(rec, file_path2, bkp_file, bkp_folder_path ,bkp_folder, err, date_today,db_content,dbfile_content)
                                            
    def get_datetime_format(self,date_time):
        # convert to datetime object
        date_time = datetime.datetime.strptime(date_time, "%Y%m%d%H%M%S").date()
        # convert to human readable date time string
        strdt = date_time.strftime("%Y-%m-%d")
        return datetime.datetime.strptime(strdt, "%Y-%m-%d").date()
    
    def sftp_upload(self,rec, file_path, bkp_file, bkp_folder_path ,bkp_folder, err, date_today,db_content,dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if err == "":
            try:
                cnopts = pysftp.CnOpts()
                cnopts.hostkeys = None
                with pysftp.Connection(host=rec.sftp_host, username=rec.sftp_user,
                                       private_key=rec.sftp_keyfilepath, cnopts=cnopts) as sftp:
                    remote = rec.sftp_file_path
                    if remote.endswith('/'):
                        remote = remote.strip('/')
                    remoteFilePath = remote + "/" + bkp_file
                    sftp.put(file_path, remoteFilePath)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if rec.backup == 'db_and_files':
                        remoteFilePath2 = remote + "/" + bkp_folder
                        sftp.put(bkp_folder_path, remoteFilePath2)
                        if os.path.exists(bkp_folder_path):
                            os.remove(bkp_folder_path)
                    _logger.info("Backup Successfully Uploaded to SFTP.")
                    StatusObj.create({'date': date_today, 'name' : "Success"})
                    if rec.backup_id.bkup_email:
                        self.send_success_mail_notificaton(rec,bkp_file,bkp_folder)
                    #remove files after x days if auto remove is true
                    if rec.backup_id.autoremove:
                        for entry in sftp.listdir_attr(remote):
                            timestamp = entry.st_mtime
                            createtime = datetime.datetime.fromtimestamp(timestamp).date()
                            date_today1 = datetime.datetime.today().date()
                            delta = date_today1 - createtime
                            if delta.days >= rec.backup_id.days_to_keep:
                                if entry.filename.endswith(".zip") or entry.filename.endswith(".dump"):
                                    filepath = remote + '/' + entry.filename
                                    if self.env.cr.dbname in entry.filename:
                                        sftp.remove(filepath)
                                    if entry.filename.endswith(".zip") and rec.backup == 'db_and_files':
                                        fpath = rec.files_path.split('/')[-1]
                                        if fpath in entry.filename:
                                            sftp.remove(filepath)
                                    _logger.info("Delete SFTP out-of-date file.")
            except Exception as err:
                _logger.debug("ERROR: %s" % (err,))
                StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec,bkp_file,bkp_folder,err)
        else:
            StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
                
    def ftp_upload(self, rec, file_path, bkp_file, bkp_folder_path, bkp_folder,err,date_today,db_content, dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if err == "":
            try:
                filename = bkp_file
                ftp = ftplib.FTP(rec.ftp_address, timeout=300)
                ftp.login(rec.ftp_usrnm, rec.ftp_pwd)
                ftp.encoding = "utf-8"
                ftp.cwd(rec.ftp_path)
                os.chdir("/tmp")
                buf=io.BytesIO(db_content)
                buf.seek(0)
                ftp.storbinary('STOR ' + filename, buf)
                if os.path.exists(file_path):
                    os.remove(file_path)
                if rec.backup == 'db_and_files':
                    buf1=io.BytesIO(dbfile_content)
                    buf1.seek(0)
                    ftp.storbinary('STOR ' + bkp_folder, buf1)
                    
                _logger.info("Backup Successfully Uploaded to FTP.")
                StatusObj.create({'date': date_today, 'name' : "Success"})
                if rec.backup == 'db_and_files':    
                    if os.path.exists(bkp_folder_path):
                        os.remove(bkp_folder_path)
                        
                if rec.backup_id.bkup_email:
                    self.send_success_mail_notificaton(rec,bkp_file,bkp_folder)
                #remove files after x days if auto remove is true
                if rec.backup_id.autoremove:
                    for file_data in ftp.mlsd():
                        file_name, meta = file_data
                        create_date = self.get_datetime_format(meta.get("modify"))
                        date_today1 = datetime.datetime.today().date()
                        delta1 = date_today1 - create_date
                        if delta1.days >= rec.backup_id.days_to_keep:
                            if file_name.endswith(".zip") or file_name.endswith(".dump"):
                                if self.env.cr.dbname in file_name:
                                    ftp.delete(file_name)
                                if file_name.endswith(".zip") and rec.backup == 'db_and_files':
                                    fpath = rec.files_path.split('/')[-1]
                                    if fpath in file_name:
                                        ftp.delete(file_name)
                                _logger.info("Delete FTP out-of-date file.")
                ftp.quit()
            except Exception as err:
                _logger.debug("ERROR: %s" % (err,))
                StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec,bkp_file,bkp_folder,err)
        else:
            StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
        
    def dropbox_upload(self, rec, file_path, bkp_file, bkp_file2, bkp_folder,status,date_today,db_content, dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if rec.dropbox_token:
            TOKEN = rec.dropbox_token 
                
            # Check for an access token
            if (len(TOKEN) == 0):
                _logger.debug("ERROR: Looks like you didn't add your access token. Please request again an authorization code.")
        
            # Create an instance of a Dropbox class, which can make requests to the API.
            dbx = dropbox.Dropbox(TOKEN,timeout=900)
            # Check that the access token is valid
            try:
                dbx.users_get_current_account()        
            except AuthError as err:
                _logger.debug("ERROR: %s" % (err,))
                
        
            # Create a backup of the current settings file
            with open(file_path, 'rb') as f:
                # We use WriteMode=overwrite to make sure that the settings in the file
                # are changed on upload
                try:
                    dbx.files_upload(f.read(),"/"+ bkp_file, mode=WriteMode('overwrite'))
                    _logger.info("Backup Successfully Uploaded to Dropbox.")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if rec.backup == 'db_only' and status == 1:
                        StatusObj.create({'date': date_today, 'name' : "Success"})
                    if rec.backup == 'db_and_files' and status == 2:
                        StatusObj.create({'date': date_today, 'name' : "Success"})
                    if rec.backup_id.bkup_email:
                        if rec.backup == 'db_only' and status == 1:
                            self.send_success_mail_notificaton(rec,bkp_file,bkp_folder)
                        if rec.backup == 'db_and_files' and status == 2:
                            self.send_success_mail_notificaton(rec,bkp_file2,bkp_folder)
                        
                except ApiError as err:
                    # This checks for the specific error where a user doesn't have enough Dropbox space quota to upload this file
                    if (err.error.is_path() and
                            err.error.get_path().error.is_insufficient_space()):
                        _logger.debug("ERROR: Cannot back up; insufficient space.")
                    elif err.user_message_text:
                        _logger.debug("ERROR: %s" % (err.user_message_text,))
                        sys.exit()
                    else:
                        _logger.debug("ERROR: %s" % (err,))
                        sys.exit()
                    StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(err))})
                    if rec.backup_id.bkup_fail_email:
                        self.send_fail_mail_notificaton(rec,bkp_file2,bkp_folder,err)
            
            #remove files after x days if auto remove is true
            if rec.backup_id.autoremove:
                for entry in dbx.files_list_folder('').entries:
                    if entry.path_lower.endswith(".zip") or entry.path_lower.endswith(".dump"):
                        date_today1 = datetime.datetime.today().date()
                        create_date = entry.client_modified.date()
                        delta1 = date_today1 - create_date
                        if delta1.days >= rec.backup_id.days_to_keep:
                            if self.env.cr.dbname in entry.name:
                                dbx.files_delete(entry.path_lower)
                            if entry.path_lower.endswith(".zip") and rec.backup == 'db_and_files':
                                fpath = rec.files_path.split('/')[-1]
                                if fpath in entry.name:
                                    dbx.files_delete(entry.path_lower)
                            _logger.info("Delete Dropbox out-of-date file.")
                
        else:
            _logger.debug("Something went wrong during the token generation. Please request again an authorization code .")
                        
    def google_drive_upload(self, rec, file_path, bkp_file,bkp_file2, bkp_folder, status, date_today,db_content,dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        g_drive = self.env['google.drive.config']
        access_token = self.get_access_token(rec,g_drive)
        # GOOGLE DRIVE UPLOAP
        if rec.backup_destination == "g_drive":
            headers = {"Authorization": "Bearer %s" % (access_token)}
            para1 = {
                "name": "%s" % (str(bkp_file)),
            }
            if status == 1:
                buf=io.BytesIO(db_content)
                buf.seek(0)
                files = {
                'data': ('metadata', json.dumps(para1), 'application/json; charset=UTF-8'),
                'file': buf,
            }
            else:
                buf=io.BytesIO(dbfile_content)
                buf.seek(0)
                files = {
                'data': ('metadata', json.dumps(para1), 'application/json; charset=UTF-8'),
                'file': buf,
            }
            r = requests.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                headers=headers,
                files=files
            )
            if r.status_code == 200:
                _logger.info("Backup Successfully Uploaded to Google Drive.")
                if os.path.exists(file_path):
                    os.remove(file_path)
                if rec.backup == 'db_only' and status == 1:
                    StatusObj.create({'date': date_today, 'name' : "Success"})
                if rec.backup == 'db_and_files' and status == 2:
                    StatusObj.create({'date': date_today, 'name' : "Success"})
                if rec.backup_id.bkup_email:
                    if rec.backup == 'db_only' and status == 1:
                            self.send_success_mail_notificaton(rec,bkp_file,bkp_folder)
                    if rec.backup == 'db_and_files' and status == 2:
                        self.send_success_mail_notificaton(rec,bkp_file2,bkp_folder)

            else:
                response = r.json()
                code = response['error']['code']
                message = response['error']['errors'][0]['message']
                reason = response['error']['errors'][0]['reason']
                _logger.debug("Backup Upload to Google drive Failed. Code: %s, Message: %s, Reason: %s" % (code, message, reason))
                StatusObj.create({'date': date_today, 'name' : "Failed (Error: %s)" % (str(message))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec,bkp_file2,bkp_folder,message)
                            
        # AUTO REMOVE UPLOADED FILE
        if rec.backup_id.autoremove:
            headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
            params = {
                'access_token': access_token,
                # 'q': "mimeType='application/zip'",
                'fields': "nextPageToken,files(id,name, createdTime, modifiedTime, mimeType)"
            }
            url = "/drive/v3/files"
            status, content, ask_time = self.env['google.service']._do_request(url, params, headers, method='GET')
            
            for item in content['files']:
                if self.env.cr.dbname in item['name']:
                    date_today1 = datetime.datetime.today().date()
                    create_date = datetime.datetime.strptime(str(item['createdTime'])[0:10], '%Y-%m-%d').date()
                    delta = date_today1 - create_date
                    if delta.days >= rec.backup_id.days_to_keep:
                        params = {
                            'access_token': access_token
                        }
                        url = "/drive/v3/files/%s" % (item['id'])
                        response = self.env['google.service']._do_request(url, params, headers, method='DELETE')
                elif rec.backup == 'db_and_files' and rec.files_path.split('/')[-1] in item['name']:
                    date_today1 = datetime.datetime.today().date()
                    create_date = datetime.datetime.strptime(str(item['createdTime'])[0:10], '%Y-%m-%d').date()
    
                    delta1 = date_today1 - create_date
                    if delta1.days >= rec.backup_id.days_to_keep:
                        url1 = "/drive/v3/files/%s" % (item['id'])
                        response1 = self.env['google.service']._do_request(url1, params, headers, method='DELETE')
                else:
                    continue

                           
    def _take_dump(self, db_name, stream, model, backup_destination,backup_format='zip'):
        """Dump database `db` into file-like object `stream` if stream is None
        return a file object with the dump """

        cron_user_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler').user_id.id
        if self._name != 'database.backup' or cron_user_id != self.env.user.id:
            _logger.error('Unauthorized database operation. Backups should only be available from the cron job.')
            raise AccessDenied()

        _logger.info('DUMP DB: %s format %s', db_name, backup_format)

        cmd = ['pg_dump', '--no-owner']
        cmd.append(db_name)

        if backup_format == 'zip':
            try:
                with tempfile.TemporaryDirectory() as dump_dir:
                    filestore = odoo.tools.config.filestore(db_name)
                    if os.path.exists(filestore):
                        shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
                    with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                        db = odoo.sql_db.db_connect(db_name)
                        with db.cursor() as cr:
                            json.dump(self._dump_db_manifest(cr), fh, indent=4)
                    cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                    odoo.tools.exec_pg_command(*cmd)
                    if stream:
                        odoo.tools.osutil.zip_dir(dump_dir, stream, include_dir=False, fnct_sort=lambda file_name: file_name != 'dump.sql')
                    else:
                        t=tempfile.TemporaryFile()
                        odoo.tools.osutil.zip_dir(dump_dir, t, include_dir=False, fnct_sort=lambda file_name: file_name != 'dump.sql')
                        t.seek(0)
                        return t
            except Exception as e:
                _logger.info("::::------ Error ----------:::: %s" %(str(e)))
        else:
            cmd.insert(-1, '--format=c')
            stdin, stdout = odoo.tools.exec_pg_command_pipe(*cmd)
            if stream:
                if backup_destination == "folder":
                    shutil.copyfileobj(stdout, stream)
                else:
                    try:
                        with open(stream, 'wb') as f:
                            f.write(stdout.read())
                    except Exception as e:
                        print("Error: ",e)
            else:
                return stdout

    def _dump_db_manifest(self, cr):
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'")
        modules = dict(cr.fetchall())
        manifest = {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }
        return manifest