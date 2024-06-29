#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: leeyoshinari

import configparser


class Config(object):
    def __init__(self):
        self.cfg = configparser.ConfigParser()
        self.cfg.read('config.conf', encoding='utf-8')

    def getServer(self, key):
        if key == 'nicSpeed':
            return self.cfg.getint('server', key, fallback=0)
        else:
            return self.cfg.get('server', key, fallback=None)

    def getLogging(self, key):
        if key == 'backupCount':
            return self.cfg.getint('logging', key, fallback=30)
        else:
            return self.cfg.get('logging', key, fallback=None)

    def getMonitor(self, key):
        if key == 'minMem':
            return self.cfg.getfloat('monitor', key, fallback=0)
        else:
            return self.cfg.getint('monitor', key, fallback=0)

    def getNginx(self, key):
        return self.cfg.get('nginx', key, fallback=None)

    def __del__(self):
        pass
