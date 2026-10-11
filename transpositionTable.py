# -*- coding: utf-8 -*-
"""
Created on Thu Apr 11 15:08:15 2024

@author: jakob
"""

class TranspositionTable:
    def __init__(self, size, size2):
        self.size = (size*1024*1024)/64
        self.size2 = (size2*1024*1024)/64
        self.entries = {}
        self.killerMoves = {}
        
    def tryLookupPosition(self, key, depth):
        try:
            entry = self.entries[key]
            if entry.depth >= depth:
                return True, entry
            return False, entry
        except:
            return None, None

    def tryLookupEval(self, key, who2move):
        try:
            entry = self.entries[key]
            if entry.who2move == who2move:
                return entry
            return None
        except:
            return None

    def checkKillerMove(self, key, move, depth): #also used for pawn hashtable
        try:
            entry = self.killerMoves[key]
            if entry.move == move and entry.depth >= depth:
                return entry.value
        except:
            pass
        return False

    
    def storeKillerMove(self, key, value, move, depth):
        try:
            entry = self.killerMoves[key]
            entry.update(move, value, depth)
            return True
        except:
            if len(self.killerMoves) < self.size2:
                self.killerMoves[key] = EntryKillerMove(move, value, depth)
                return True
            self.replaceKiller(key, value, move, depth)
    
    def storeEvaluation(self, key, value, move, depth):
        try:
            entry = self.entries[key]
            entry.update(value, move, depth)
            return True
        except:
            if len(self.entries) < self.size:
                self.entries[key] = Entry(value, move, depth)
                return True
            self.replace(key, value, move, depth)

    def storePositionEval(self, key, value, who2move):
        if len(self.entries) < self.size:
            self.entries[key] = EntryPositionEval(value, who2move)
            return True
        else:
            try:
                entry = self.entries[key]
                entry.update(value, who2move)
                return True
            except:
                self.replaceEval(key, value, who2move)

    def replace(self, key, value, move, depth):
        del self.entries[list(self.entries.keys())[0]]    
        self.storeEvaluation(key, value, move, depth)

    def replaceEval(self, key, value, who2move):
        del self.entries[list(self.entries.keys())[0]]
        self.storePositionEval(key, value, who2move)

    def replaceKiller(self, key, value, move, depth):
        del self.killerMoves[list(self.killerMoves.keys())[0]]
        self.storeKillerMove(key, value, move, depth)
    
class Entry:
    def __init__(self, value, move, depth):
        self.value = value
        self.move = move
        self.depth = depth
        
    def update(self, value, move, depth):
        if depth > self.depth:
            self.value = value
            self.move = move
            self.depth = depth


class EntryPositionEval:
    def __init__(self, value, who2move):
        self.value = value
        self.who2move = who2move

    def update(self, value, who2move):
        self.value = value
        self.who2move = who2move

class EntryKillerMove:
    def __init__(self, move, value, depth):
        self.move = move
        self.depth = depth
        self.value = value

    def update(self, move, value, depth):
        if depth >= self.depth and value > self.value:
            self.move = move
            self.depth = depth
            self.value = value
