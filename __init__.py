#!/usr/bin/env python

__license__   = 'GPL v3'
__copyright__ = '2025, un_pogaz <un.pogaz@gmail.com>'


try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

# The class that all Interface Action plugin wrappers must inherit from
from calibre.customize import InterfaceActionBase


class ActionCSVMetadata(InterfaceActionBase):
    '''
    This class is a simple wrapper that provides information about the actual
    plugin class. The actual interface plugin class is called InterfacePlugin
    and is defined in the ui.py file, as specified in the actual_plugin field
    below.
    
    The reason for having two classes is that it allows the command line
    calibre utilities to run without needing to load the GUI libraries.
    '''
    name                    = 'CSV Update Metadata'
    description             = _('Update Metadata from a CSV file template')
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'un_pogaz'
    version                 = (0, 1, 0)
    minimum_calibre_version = (5, 0, 0)
    
    actual_plugin = __name__+'.action:CSVMetadataAction'
    
    def initialize(self):
        '''
        Called once when calibre plugins are initialized.  Plugins are
        re-initialized every time a new plugin is added. Also note that if the
        plugin is run in a worker process, such as for adding books, then the
        plugin will be initialized for every new worker process.
        
        Perform any plugin specific initialization here, such as extracting
        resources from the plugin ZIP file. The path to the ZIP file is
        available as ``self.plugin_path``.
        
        Note that ``self.site_customization`` is **not** available at this point.
        '''
        pass
    
    def is_customizable(self):
        '''
        This method must return True to enable customization via
        Preferences->Plugins
        '''
        return False
