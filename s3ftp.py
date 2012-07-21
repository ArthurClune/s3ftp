#!/usr/bin/env python

"""SFTP-like Amazon S3 client.

S3 does not natively support directories; we fake a directory tree
for navigation
"""


# Original code
# Copyright 2006 Dug Song <dugsong@monkey.org>
# This version modified by Arthur Clune <arthur@honeynet.org.uk>
# Code license: GPL

__version__ = '0.2'

# $Id$

import cStringIO, inspect, os, readline, shlex, sys 

# use readline where available               
try:
    import readline
except:
    pass

from boto.s3.connection import S3Connection
from boto.s3.key import Key    
from boto.exception import S3ResponseError, S3CreateError

# XXX - fill these in, or add a keys.py file or whatever
ACCESS_KEY = ''
SECRET_KEY = ''
if not SECRET_KEY:
    try:
        from keys import ACCESS_KEY, SECRET_KEY
    except ImportError:
        def _get_s3key(k):
            fds = os.popen3('security find-internet-password ' \
                            '-g -s s3.amazonaws.com -d %s' % k)
            return shlex.split(fds[2].readline())[1]
        ACCESS_KEY = _get_s3key('access')
        SECRET_KEY = _get_s3key('secret')

# XXX - TODO:
# support s3rsync's owner/perms/times metadata

class S3ftp(object):
    def __init__(self, access_key, secret_key, bucket):
        self.conn = S3Connection(ACCESS_KEY, SECRET_KEY)
        r = self.conn.get_all_buckets()
        self.cwd = '/'  
        if bucket not in [ e.name for e in r ]:    
            try:
                r = self.conn.create_bucket(bucket)       
                self.bucket = r
            except (S3ResponseError, S3CreateError), e:
                self.perror(e)
                sys.exit(1)         
        else:
            self.bucket = self.conn.get_bucket(bucket)
        print 'Connected to S3, bucket "%s"' % self.bucket.name
    
    def perror(self, r): 
        print "Error: %s" % r.reason

    def _path_to_prefix(self, path):
        path = path.lstrip('/')
        if path:
            path += '/'
        return path

    def _path_to_key(self, path):
        return path.lstrip('/')
    
    def cmd_invalid(self, *args):
        print >>sys.stderr, 'Invalid command.'

    def cmd_help(self, *args):
        """display this help text"""
        for x in dir(self):
            if x.startswith('cmd_'):
                l = [ x[4:] ]
                f = getattr(self, x)
                args, varargs, varkw, defaults = inspect.getargspec(f)
                n = len(args or []) - len(defaults or [])
                for a in args[1:n]:
                    l.append('<%s>' % a)
                for a in args[n:]:
                    if not a.startswith('_'):
                        l.append('[%s]' % a)
                if f.__doc__:
                    print '%-24s %s' % (' '.join(l), f.__doc__)

    def normpath(self, path):    
        path = os.path.expanduser(path)
        if not path.startswith('/'):
            path = '%s/%s' % (self.cwd, path)
        path = os.path.normpath(path)
        if path.startswith('//'):
            path = path[1:]	# XXX - os.path.normpath bug
        return path

    def cmd_cd(self, path):
        """change remote directory"""
        path = self.normpath(path)
        prefix = self._path_to_prefix(path)
        delimiter = '/' 
        r = self.bucket.get_all_keys(prefix=prefix, delimiter=delimiter)
        if len(r):
            self.cwd = path
        else:
            print 'No such directory'
    
    def cmd_lcd(self, path='~'):
        """change local directory"""
        path = os.path.expanduser(path)
        try:
            os.chdir(path)
        except OSError, msg:
            print 'Couldn\'t change local directory to "%s": %s' % \
                  (path, msg[1])
    
    def cmd_get(self, rpath, lpath = None):
        """download a file"""
        path = self._path_to_key(self.normpath(rpath))
        k = Key(self.bucket)
        k.key = path                
        if lpath:
            k.get_contents_to_filename(lpath)
        else:
            k.get_contents_to_filename(os.path.basename(rpath))
    
    def cmd_getdir(self, path):
        """recursively download a directory"""
        path = self.normpath(path)
        prefix = self._path_to_prefix(path)                
        delimiter = '/'    
        os.mkdir(os.path.dirname(prefix))
        r = self.bucket.get_all_keys(prefix=prefix, delimiter=delimiter)
        for k in r:        
            print 'Getting %s.... ' % k.name
            if hasattr(k, 'CommonPrefixes'):
                self.cmd_getdir(k.name)
            else:         
                self.cmd_get(k.name, k.name)  
                
    def cmd_lpwd(self):
        """print local working directory"""
        print 'Local working directory:', os.getcwd()

    def cmd_lls(self, *args):
        """display local directory listing""" 
        if not args:
            print '\t'.join(os.listdir('.'))
            return
        for dir in args:               
            print '%s:' % dir
            print '\t'.join(os.listdir(dir))
            print

    def cmd_ls(self, path='', _path2=''):
        """list files / directories. Options -l or -e"""
        acls = False
        verbose = False
        if path == '-l':
            prefix = self._path_to_prefix(self.normpath(_path2))
            verbose = True                           
        elif path == '-e':
            prefix = self._path_to_prefix(self.normpath(_path2))
            verbose = True
            acls = True            
        else:
            prefix = self._path_to_prefix(self.normpath(path))
        delimiter = '/' 
        if not prefix:
            prefix = ''
        r = self.bucket.get_all_keys(prefix=prefix, delimiter=delimiter)
        if not verbose:   
            l = []
            for k in r: 
                if hasattr(k, 'CommonPrefixes'):  
                    l.append(os.path.basename(k.name[:-1]) + '/')
                else:
                    l.append(os.path.basename(k.key))
            print ' '.join(l)
            return
        if verbose:
            for k in r:    
                if hasattr(k, 'CommonPrefixes'):
                    print 'drwx------\t%s ' % \
                        (k.name)
                else:   
                    acllist = []
                    public_read = False
                    public_write = False
                    perms = k.get_acl()
                    for g in perms.acl.grants:
                        if g.type == u'CanonicalUser':
                            acllist.append('\t%s %s\n' % (g.display_name, g.permission))
                        elif g.type == u'Group':
                            group = '/'.join(g.uri.split('/')[4:])
                            acllist.append('\t%s %s\n' % (group, g.permission))
                            if group == 'global/AllUsers':
                                if g.permission == 'READ':
                                    public_read = True
                                if g.permission == 'WRITE':
                                    public_write = True
                    if public_write: 
                        print '-rw-rw-rw-\t', 
                    elif public_write:                     
                        print '-rw-r--r--\t',
                    else:
                        print '-rw-------\t',
                    print '%s\t%s\t%s\t%s' % \
                          (k.owner.display_name, k.size, k.last_modified,
                           k.key[len(prefix):])  
                    if acls:
                        print ' '.join(acllist),
    
    def cmd_lsdir(self, path=''):
        """recursively list a directory tree"""
        options = {}
        prefix = self._path_to_prefix(self.normpath(path))  
        r = self.bucket.get_all_keys(prefix=prefix)
        for k in r:
            print k.key[len(prefix):]

    def cmd_mkdir(self, path):
        """create a directory"""
        key = self._path_to_key(self.normpath(path))
        input = cStringIO.StringIO('')
        k = Key(self.bucket)
        k.name = '%s/.s3ftp_marker' % key
        k.set_contents_from_file(input)        
    
    def cmd_put(self, lpath, rpath=''):
        """upload file"""  
        if not rpath:
            rpath = self.normpath('%s/%s' % (
                self.cwd, os.path.basename(lpath)))
        key = self._path_to_key(self.normpath(rpath))
        print 'Uploading', lpath, 'to', key
        try:
            k = Key(self.bucket)
            k.name = key    
            k.set_contents_from_filename(lpath)
        except IOError, e:
            print e
    
    def cmd_pwd(self):
        """print working directory on remote machine"""
        print 'Remote working directory: %s' % self.cwd              
    
    def cmd_putdir(self, path):
        """recursively upload a directory tree"""
        path = os.path.normpath(path)
        for subdir, dirs, files in os.walk(path, topdown=True):
            subdir = os.path.basename(subdir)
            self.cmd_mkdir(subdir)
            self.cmd_lcd(subdir)
            self.cmd_cd(subdir)       
            for file in files:
                self.cmd_put(file)                       
            for dir in dirs:
                self.cmd_putdir(dir)
                dirs.remove(dir)                
            self.cmd_lcd('..')
            self.cmd_cd('..')

    def cmd_rm(self, path):
        """delete remote file"""  
        if path=='*':
            prefix = self._path_to_prefix(self.normpath(self.cwd))
            r = self.bucket.get_all_keys(prefix=prefix, delimiter='/')
            for k in r:
                if not hasattr(k, 'CommonPrefixes'):
                    k.delete()
        else:
            key = self._path_to_key(self.normpath(path))
            print 'Removing', path        
            k = Key(self.bucket)
            k.key = key
            k.delete() 
    
    def cmd_rmdir(self, path):
        """recursively remove a directory tree"""
        delimiter = '/'
        prefix = self._path_to_prefix(self.normpath(path))
        r = self.bucket.get_all_keys(prefix=prefix, delimiter=delimiter)
        for k in r:      
            if hasattr(k, 'CommonPrefixes'):
                self.cmd_rmdir(k.name)
            else:
                k.delete()
      
    def cmd_setacl(self, acl, path):
        """set acl: acl = public-read | public-read-write | private""" 
        key = self._path_to_key(self.normpath(rpath))
        try:
            k = Key(self.bucket)
            k.key = key
            k.set_acl(acl)
        except AssertionError:
            print 'Bad acl specifier. Valid forms are public-read, public-read-write or private'

    def cmd_version(self):
        """print version"""
        print 's3ftp ', __version__
    
    def cmd_exit(self):
        """leave s3ftp"""
        sys.exit(0)
    cmd_quit = cmd_exit
    
    def main(self):
        while 1:
            try:
                l = shlex.split(raw_input('s3ftp> ')) or [ '' ]
            except (EOFError, KeyboardInterrupt):
                break              
            if l[0].startswith('!'):
                os.system(l[0][1:])
            elif l == ['']:
                continue
            else:
                m = getattr(self, 'cmd_%s' % l[0], self.cmd_invalid)
                try:
                    m(*l[1:])
                except TypeError, e:                               
                    print e  
                    print 'Wrong arguments for %s, try help' % (l[0])  
                except S3ResponseError, e:
                    self.perror(e) 

if __name__ == '__main__':
    if len(sys.argv) == 2:
        bucket = sys.argv[1]
    else:
        bucket = os.getenv('USER')
    S3ftp(ACCESS_KEY, SECRET_KEY, bucket).main()
