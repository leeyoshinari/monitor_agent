#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: leeyoshinari

import configparser


class Config(object):
    def __init__(self):
        self.cfg = configparser.ConfigParser()
        self.cfg.read('config.ini', encoding='utf-8')

    def getAgent(self, key):
        if key == 'threadPool' or key == 'nicSpeed':
            return self.cfg.getint('agent', key, fallback=0)
        else:
            return self.cfg.get('agent', key, fallback=None)

    def getInflux(self, key):
        return self.cfg.get('influx', key, fallback=None)

    def getServer(self, key):
        return self.cfg.get('server', key, fallback=None)

    def getLogging(self, key):
        if key == 'backupCount':
            return self.cfg.getint('logging', key, fallback=30)
        else:
            return self.cfg.get('logging', key, fallback=None)

    def getMonitor(self, key):
        if key == 'minMem':
            return self.cfg.getfloat('monitor', key, fallback=0)
        elif key == 'timeSetting':
            return self.cfg.get('monitor', key, fallback='05:20')
        else:
            return self.cfg.getint('monitor', key, fallback=0)

    def __del__(self):
        pass
