#! /usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import time
import os
import shutil
from xml.etree import cElementTree as ET
import plistlib
import textwrap
import datetime
import glob
from collections import OrderedDict

"""
- filter out unknown attributes and subelements
- add doctests for the image purging
- things that need to be improved are marked with "# TO DO"
"""

__version__ = "0a1"
description = """
UFO Normalizer (version %s):

This tool processes the contents of a UFO and normalizes
all possible files to a standard XML formatting, data
structure and file naming scheme.
""" % __version__

def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("input", help="Path to a UFO to normalize.", nargs="?")
    parser.add_argument("-t", "--test", help="Run the normalizer's internal tests.", action="store_true")
    parser.add_argument("-o", "--output", help="Output path. If not given, the input path will be used.")
    parser.add_argument("-a", "--all", help="Normalize all files in the UFO. By default, only files modified since the previous normalization will be processed.", action="store_true")
    args = parser.parse_args(args)
    if args.test:
        runTests()
        return
    inputPath = args.input
    outputPath = args.output
    onlyModified = not args.all
    if inputPath is None:
        print("No input path was specified.")
        return
    if not os.path.exists(inputPath):
        print("Input path does not exist:", inputPath)
        return
    if os.path.splitext(inputPath)[-1].lower() != ".ufo":
        print("Input path is not a UFO:", inputPath)
        return
    message = "Normalizing \"%s\"." % os.path.basename(inputPath)
    if not onlyModified:
        message += " Processing all files."
    print(message)
    start = time.time()
    normalizeUFO(inputPath, outputPath=outputPath, onlyModified=onlyModified)
    runtime = time.time() - start
    print("Normalization complete (%.4f seconds)." % runtime)

# ---------
# Internals
# ---------

modTimeLibKey = "org.unifiedfontobject.normalizer.modTimes"
imageReferencesLibKey = "org.unifiedfontobject.normalizer.imageReferences"

# Differences between Python 2 and Python 3
# Python 3 does not have long, basestring, unicode
try:

    long
except NameError:
    long = int

try:
    basestring
except NameError:
    basestring = str

try:
    unicode
except NameError:
    unicode = str

# Python2 does not have plistlib.readPlistFromBytes it has
# plistlib.readPlistFromString instead.
try:
    plistlib.readPlistFromBytes

    def _readPlistFromBytes(data):
        return plistlib.readPlistFromBytes(tobytes(data))

    def _writePlistToBytes(plist):
        return plistlib.writePlistToBytes(plist)
except AttributeError:
    def _readPlistFromBytes(data):
        return plistlib.readPlistFromString(data)

    def _writePlistToBytes(plist):
        return plistlib.writePlistToString(plist)


# from fontTools.misc.py23
def tobytes(s, encoding='ascii', errors='strict'):
    '''no docstring'''
    if not isinstance(s, bytes):
        return s.encode(encoding, errors)
    else:
        return s


def tounicode(s, encoding='ascii', errors='strict'):
    if not isinstance(s, unicode):
        return s.decode(encoding, errors)
    else:
        return s

if str == bytes:
    tostr = tobytes
else:
    tostr = tounicode


class UFONormalizerError(Exception): pass


def normalizeUFO(ufoPath, outputPath=None, onlyModified=True):
    # if the output is going to a different location,
    # duplicate the UFO to the new place and work
    # on the new file instead of trying to reconstruct
    # the file one piece at a time.
    if outputPath is not None:
        duplicateUFO(ufoPath, outputPath)
        ufoPath = outputPath
    # get the UFO format version
    if not subpathExists(ufoPath, "metainfo.plist"):
        raise UFONormalizerError("Required metainfo.plist file not in %s." % ufoPath)
    metaInfo = subpathReadPlist(ufoPath, "metainfo.plist")
    formatVersion = metaInfo.get("formatVersion")
    if formatVersion is None:
        raise UFONormalizerError("Required formatVersion value not defined in in metainfo.plist in %s." % ufoPath)
    try:
        fV = int(formatVersion)
        formatVersion = fV
    except ValueError:
        raise UFONormalizerError("Required formatVersion value not properly formatted in metainfo.plist in %s." % ufoPath)
    if formatVersion > 3:
        raise UFONormalizerError("Unsupported UFO format (%d) in %s." % (formatVersion, ufoPath))
    # load the font lib
    if not subpathExists(ufoPath, "lib.plist"):
        fontLib = {}
    else:
        fontLib = subpathReadPlist(ufoPath, "lib.plist")
    # get the modification times
    if onlyModified:
        modTimes = readModTimes(fontLib)
    else:
        modTimes = {}
    # normalize layers
    if formatVersion < 3:
        if subpathExists(ufoPath, "glyphs"):
            normalizeUFO1And2GlyphsDirectory(ufoPath, modTimes)
    else:
        availableImages = readImagesDirectory(ufoPath)
        referencedImages = set()
        normalizeGlyphsDirectoryNames(ufoPath)
        if subpathExists(ufoPath, "layercontents.plist"):
            layerContents = subpathReadPlist(ufoPath, "layercontents.plist")
            for layerName, layerDirectory in layerContents:
                layerReferencedImages = normalizeGlyphsDirectory(ufoPath, layerDirectory, onlyModified=onlyModified)
                referencedImages |= layerReferencedImages
        imagesToPurge = availableImages - referencedImages
        purgeImagesDirectory(ufoPath, imagesToPurge)
    # normalize top level files
    normalizeMetaInfoPlist(ufoPath, modTimes)
    if subpathExists(ufoPath, "fontinfo.plist"):
        normalizeFontInfoPlist(ufoPath, modTimes)
    if subpathExists(ufoPath, "groups.plist"):
        normalizeGroupsPlist(ufoPath, modTimes)
    if subpathExists(ufoPath, "kerning.plist"):
        normalizeKerningPlist(ufoPath, modTimes)
    if subpathExists(ufoPath, "layercontents.plist"):
        normalizeLayerContentsPlist(ufoPath, modTimes)
    # update the mod time storage, write, normalize
    storeModTimes(fontLib, modTimes)
    subpathWritePlist(fontLib, ufoPath, "lib.plist")
    if subpathExists(ufoPath, "lib.plist"):
        normalizeLibPlist(ufoPath)

# ------
# Layers
# ------

def normalizeGlyphsDirectoryNames(ufoPath):
    """
    Normalize glyphs directory names following
    UFO 3 user name to file name convention.

    non-standard directory names
    -----------------------------
    >>> oldLayers = [
    ...     ("public.default", "glyphs"),
    ...     ("Sketches", "glyphs.sketches"),
    ... ]
    >>> expectedLayers = [
    ...     ("public.default", "glyphs"),
    ...     ("Sketches", "glyphs.S_ketches"),
    ... ]
    >>> _test_normalizeGlyphsDirectoryNames(oldLayers, expectedLayers)
    True

    old directory with same name as new directory
    ---------------------------------------------
    >>> oldLayers = [
    ...     ("public.default", "glyphs"),
    ...     ("one", "glyphs.two"),
    ...     ("two", "glyphs.three")
    ... ]
    >>> expectedLayers = [
    ...     ("public.default", "glyphs"),
    ...     ("one", "glyphs.one"),
    ...     ("two", "glyphs.two")
    ... ]
    >>> _test_normalizeGlyphsDirectoryNames(oldLayers, expectedLayers)
    True
    """
    # INVALID DATA POSSIBILITY: directory for layer name may not exist
    # INVALID DATA POSSIBILITY: directory may not be stored in layer contents
    oldLayerMapping = OrderedDict()
    if subpathExists(ufoPath, "layercontents.plist"):
        layerContents = subpathReadPlist(ufoPath, "layercontents.plist")
        for layerName, layerDirectory in layerContents:
            oldLayerMapping[layerName] = layerDirectory
    if not oldLayerMapping:
        return
    # INVALID DATA POSSIBILITY: no default layer
    # INVALID DATA POSSIBILITY: public.default used for directory other than "glyphs"
    newLayerMapping = OrderedDict()
    newLayerDirectories = set()
    for layerName, oldLayerDirectory in oldLayerMapping.items():
        if oldLayerDirectory == "glyphs":
            newLayerDirectory = "glyphs"
        else:
            newLayerDirectory = userNameToFileName(unicode(layerName), newLayerDirectories, prefix="glyphs.")
        newLayerDirectories.add(newLayerDirectory)
        newLayerMapping[layerName] = newLayerDirectory
    # don't do a direct rename because an old directory
    # may have the same name as a new directory.
    fromTempMapping = {}
    for index, (layerName, newLayerDirectory) in enumerate(newLayerMapping.items()):
        oldLayerDirectory = oldLayerMapping[layerName]
        if newLayerDirectory == oldLayerDirectory:
            continue
        tempDirectory = "org.unifiedfontobject.normalizer.%d" % index
        subpathRenameDirectory(ufoPath, oldLayerDirectory, tempDirectory)
        fromTempMapping[tempDirectory] = newLayerDirectory
    for tempDirectory, newLayerDirectory in fromTempMapping.items():
        subpathRenameDirectory(ufoPath, tempDirectory, newLayerDirectory)
    # update layercontents.plist
    newLayerMapping = list(newLayerMapping.items())
    subpathWritePlist(newLayerMapping, ufoPath, "layercontents.plist")
    return newLayerMapping

def _test_normalizeGlyphsDirectoryNames(oldLayers, expectedLayers):
    import tempfile
    directory = tempfile.mkdtemp()
    for layerName, subDirectory in oldLayers:
        os.mkdir(os.path.join(directory, subDirectory))
    assert sorted(os.listdir(directory)) == sorted([oldDirectory for oldName, oldDirectory in oldLayers])
    subpathWritePlist(oldLayers, directory, "layercontents.plist")
    newLayers = normalizeGlyphsDirectoryNames(directory)
    listing = os.listdir(directory)
    listing.remove("layercontents.plist")
    assert sorted(listing) == sorted([newDirectory for newName, newDirectory in newLayers])
    shutil.rmtree(directory)
    return newLayers == expectedLayers

# ------
# Glyphs
# ------

def normalizeUFO1And2GlyphsDirectory(ufoPath, modTimes):
    glyphMapping = normalizeGlyphNames(ufoPath, "glyphs")
    for fileName in sorted(glyphMapping.values()):
        location = subpathJoin("glyphs", fileName)
        if subpathNeedsRefresh(modTimes, ufoPath, location):
            normalizeGLIF(ufoPath, "glyphs", fileName)
            modTimes[location] = subpathGetModTime(ufoPath, "glyphs", fileName)

def normalizeGlyphsDirectory(ufoPath, layerDirectory, onlyModified=True):
    if subpathExists(ufoPath, layerDirectory, "layerinfo.plist"):
        layerInfo = subpathReadPlist(ufoPath, layerDirectory, "layerinfo.plist")
        layerLib = layerInfo.get("lib", {})
    else:
        layerLib = {}
    imageReferences = {}
    if onlyModified:
        stored = readImageReferences(layerLib)
        if stored is not None:
            imageReferences = stored
        else:
            # we don't know what has a reference so we must check everything
            onlyModified = False
    if onlyModified:
        modTimes = readModTimes(layerLib)
    else:
        modTimes = {}
    glyphMapping = normalizeGlyphNames(ufoPath, layerDirectory)
    for fileName in glyphMapping.values():
        if subpathNeedsRefresh(modTimes, ufoPath, layerDirectory, fileName):
            imageFileName = normalizeGLIF(ufoPath, layerDirectory, fileName)
            if imageFileName is not None:
                imageReferences[fileName] = imageFileName
            elif fileName in imageReferences:
                del imageReferences[fileName]
            modTimes[fileName] = subpathGetModTime(ufoPath, layerDirectory, fileName)
    storeModTimes(layerLib, modTimes)
    storeImageReferences(layerLib, imageReferences)
    normalizeLayerInfoPlist(ufoPath, layerDirectory)
    referencedImages = set(imageReferences.values())
    return referencedImages

def normalizeLayerInfoPlist(ufoPath, layerDirectory):
    if subpathExists(ufoPath, layerDirectory, "layerinfo.plist"):
        _normalizePlistFile({}, ufoPath, *[layerDirectory, "layerinfo.plist"], preprocessor=_normalizeLayerInfoColor)

def _normalizeLayerInfoColor(obj):
    """
    - Normalize the color if specified.

    >>> obj = dict(color="1,0,0,.5")
    >>> _normalizeLayerInfoColor(obj)
    >>> obj
    {'color': '1,0,0,0.5'}

    >>> obj = dict(color="invalid")
    >>> _normalizeLayerInfoColor(obj)
    >>> obj
    {}
    """
    if "color" in obj:
        color = obj.pop("color")
        color = _normalizeColorString(color)
        if color is not None:
            obj["color"] = color

def normalizeGlyphNames(ufoPath, layerDirectory):
    """
    Normalize GLIF file names following
    UFO 3 user name to file name convention.

    non-standard file names
    -----------------------
    >>> oldNames = {
    ...     "A" : "a.glif",
    ...     "B" : "b.glif"
    ... }
    >>> expectedNames = {
    ...     "A" : "A_.glif",
    ...     "B" : "B_.glif"
    ... }
    >>> _test_normalizeGlyphNames(oldNames, expectedNames)
    True

    old file with same name as new file
    -----------------------------------
    >>> oldNames = {
    ...     "one" : "two.glif",
    ...     "two" : "three.glif"
    ... }
    >>> expectedNames = {
    ...     "one" : "one.glif",
    ...     "two" : "two.glif"
    ... }
    >>> _test_normalizeGlyphNames(oldNames, expectedNames)
    True
    """
    # INVALID DATA POSSIBILITY: no contents.plist
    # INVALID DATA POSSIBILITY: file for glyph name may not exist
    # INVALID DATA POSSIBILITY: file for glyph may not be stored in contents
    if not subpathExists(ufoPath, layerDirectory, "contents.plist"):
        return {}
    oldGlyphMapping = subpathReadPlist(ufoPath, layerDirectory, "contents.plist")
    newGlyphMapping = {}
    newFileNames = set()
    for glyphName in sorted(oldGlyphMapping.keys()):
        newFileName = userNameToFileName(unicode(glyphName), newFileNames, suffix=".glif")
        newFileNames.add(newFileName)
        newGlyphMapping[glyphName] = newFileName
    # don't do a direct rewrite in case an old file has
    # the same name as a new file.
    fromTempMapping = {}
    for index, (glyphName, newFileName) in enumerate(sorted(newGlyphMapping.items())):
        oldFileName = oldGlyphMapping[glyphName]
        if newFileName == oldFileName:
            continue
        tempFileName = "org.unifiedfontobject.normalizer.%d" % index
        subpathRenameFile(ufoPath, (layerDirectory, oldFileName), (layerDirectory, tempFileName))
        fromTempMapping[tempFileName] = newFileName
    for tempFileName, newFileName in fromTempMapping.items():
        subpathRenameFile(ufoPath, (layerDirectory, tempFileName), (layerDirectory, newFileName))
    # update contents.plist
    subpathWritePlist(newGlyphMapping, ufoPath, layerDirectory, "contents.plist")
    # normalize contents.plist
    _normalizePlistFile({}, ufoPath, layerDirectory, "contents.plist")
    return newGlyphMapping

def _test_normalizeGlyphNames(oldGlyphMapping, expectedGlyphMapping):
    import tempfile
    directory = tempfile.mkdtemp()
    layerDirectory = "glyphs"
    fullLayerDirectory = subpathJoin(directory, layerDirectory)
    os.mkdir(fullLayerDirectory)
    for fileName in oldGlyphMapping.values():
        subpathWriteFile("", directory, layerDirectory, fileName)
    assert sorted(os.listdir(fullLayerDirectory)) == sorted(oldGlyphMapping.values())
    subpathWritePlist(oldGlyphMapping, directory, layerDirectory, "contents.plist")
    newGlyphMapping = normalizeGlyphNames(directory, layerDirectory)
    listing = os.listdir(fullLayerDirectory)
    listing.remove("contents.plist")
    assert sorted(listing) == sorted(newGlyphMapping.values())
    assert subpathReadPlist(directory, layerDirectory, "contents.plist") == newGlyphMapping
    shutil.rmtree(directory)
    return newGlyphMapping == expectedGlyphMapping

# ---------------
# Top-Level Files
# ---------------

# These are broken into separate, file specific
# functions for clarity and in case file specific
# normalization (such as filtering default values)
# needs to occur.

def _normalizePlistFile(modTimes, ufoPath, *subpath, **kwargs):
    if subpathNeedsRefresh(modTimes, ufoPath, *subpath):
        preprocessor = kwargs.get("preprocessor")
        data = subpathReadPlist(ufoPath, *subpath)
        if data:
            text = normalizePropertyList(data, preprocessor=preprocessor)
            subpathWriteFile(text, ufoPath, *subpath)
            modTimes[subpath[-1]] = subpathGetModTime(ufoPath, *subpath)
        # Don't write empty plist files.
        else:
            subpathRemoveFile(ufoPath, *subpath)
            if subpath[-1] in modTimes:
                del modTimes[subpath[-1]]

# metainfo.plist

def normalizeMetaInfoPlist(ufoPath, modTimes):
    _normalizePlistFile(modTimes, ufoPath, "metainfo.plist")

# fontinfo.plist

def normalizeFontInfoPlist(ufoPath, modTimes):
    _normalizePlistFile(modTimes, ufoPath, "fontinfo.plist", preprocessor=_normalizeFontInfoGuidelines)

def _normalizeFontInfoGuidelines(obj):
    r"""
    - Follow general guideline normalization rules.

    >>> test = '''\
    ... <plist version="1.0">
    ...     <dict>
    ...         <key>guidelines</key>
    ...         <array>
    ...             <dict>
    ...                 <key>x</key><integer>1</integer>
    ...                 <key>y</key><integer>2</integer>
    ...                 <key>angle</key><integer>3</integer>
    ...                 <key>color</key><string>1,0,0,.5</string>
    ...             </dict>
    ...             <dict>
    ...                 <key>x</key><integer>4</integer>
    ...                 <key>y</key><integer>5</integer>
    ...                 <key>angle</key><integer>6</integer>
    ...                 <key>color</key><string>0,1,0,.5</string>
    ...             </dict>
    ...             <dict>
    ...                 <key>x</key><integer>7</integer>
    ...                 <key>y</key><integer>8</integer>
    ...                 <key>angle</key><integer>9</integer>
    ...                 <key>color</key><string>invalid</string>
    ...             </dict>
    ...         </array>
    ...     </dict>
    ... </plist>
    ... '''
    >>> expected = {
    ...     "guidelines" : [
    ...         dict(x=1, y=2, angle=3, color="1,0,0,0.5"),
    ...         dict(x=4, y=5, angle=6, color="0,1,0,0.5"),
    ...         dict(x=7, y=8, angle=9),
    ...     ]
    ... }
    >>> plist = _readPlistFromBytes(test)
    >>> _normalizeFontInfoGuidelines(plist)
    >>> plist == expected
    True

    no guidelines
    -------------
    >>> test = '''\
    ... <plist version="1.0">
    ...     <dict>
    ...         <key>guidelines</key>
    ...         <array/>
    ...     </dict>
    ... </plist>
    ... '''
    >>> plist = _readPlistFromBytes(test)
    >>> _normalizeFontInfoGuidelines(plist)
    """
    guidelines = obj.get("guidelines")
    if not guidelines:
        return
    normalized = []
    for guideline in guidelines:
        guideline = _normalizeDictGuideline(guideline)
        if guideline is not None:
            normalized.append(guideline)
    obj["guidelines"] = normalized

def _normalizeDictGuideline(guideline):
    """
    - Don't write if angle is defined but either x or y are not defined.
    - Don't write if both x and y are defined but angle is not defined.

    everything
    ----------
    >>> guideline = dict(x=1, y=2, angle=3, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = dict(x=1, y=2, angle=3, name="test", color="1,0,0,0.5", identifier="TEST")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no x
    ----
    >>> guideline = dict(y=2, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = dict(y=2, name="test", color="1,0,0,0.5", identifier="TEST")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    >>> guideline = dict(y=2, angle=3, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    invalid x
    ---------
    >>> guideline = dict(x="invalid", y=2, angle=3, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no y
    ----
    >>> guideline = dict(x=1, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = dict(x=1, name="test", color="1,0,0,0.5", identifier="TEST")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    >>> guideline = dict(x=1, angle=3, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    invalid y
    ---------
    >>> guideline = dict(x=1, y="invalid", angle=3, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no angle
    --------
    >>> guideline = dict(x=1, y=2, name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    invalid angle
    -------------
    >>> guideline = dict(x=1, y=3, angle="invalid", name="test", color="1,0,0,.5", identifier="TEST")
    >>> expected = None
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no name
    -------
    >>> guideline = dict(x=1, y=2, angle=3, color="1,0,0,.5", identifier="TEST")
    >>> expected = dict(x=1, y=2, angle=3, color="1,0,0,0.5", identifier="TEST")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no color
    --------
    >>> guideline = dict(x=1, y=2, angle=3, name="test", identifier="TEST")
    >>> expected = dict(x=1, y=2, angle=3, name="test", identifier="TEST")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True

    no identifier
    -------------
    >>> guideline = dict(x=1, y=2, angle=3, name="test", color="1,0,0,.5")
    >>> expected = dict(x=1, y=2, angle=3, name="test", color="1,0,0,0.5")
    >>> result = _normalizeDictGuideline(guideline)
    >>> result == expected
    True
    """
    x = guideline.get("x")
    y = guideline.get("y")
    angle = guideline.get("angle")
    name = guideline.get("name")
    color = guideline.get("color")
    identifier = guideline.get("identifier")
    # either x or y must be defined
    if not x and not y:
        return
    # if angle is specified, x and y must be specified
    if (not x or not y) and angle:
        return
    # if x and y are specified, angle must be specified
    if (x and y) and not angle:
        return
    # value errors
    if x:
        try:
            x = float(x)
        except ValueError:
            return
    if y:
        try:
            y = float(y)
        except ValueError:
            return
    if angle:
        try:
            angle = float(angle)
        except ValueError:
            return
    normalized = {}
    if x is not None:
        normalized["x"] = x
    if y is not None:
        normalized["y"] = y
    if angle is not None:
        normalized["angle"] = angle
    if name is not None:
        normalized["name"] = name
    if color is not None:
        color = _normalizeColorString(color)
        if color is not None:
            normalized["color"] = color
    if identifier is not None:
        normalized["identifier"] = identifier
    return normalized

# groups.plist

def normalizeGroupsPlist(ufoPath, modTimes):
    _normalizePlistFile(modTimes, ufoPath, "groups.plist")

# kerning.plist

def normalizeKerningPlist(ufoPath, modTimes):
    _normalizePlistFile(modTimes, ufoPath, "kerning.plist")

# layercontents.plist

def normalizeLayerContentsPlist(ufoPath, modTimes):
    _normalizePlistFile(modTimes, ufoPath, "layercontents.plist")

# lib.plist

def normalizeLibPlist(ufoPath):
    _normalizePlistFile({}, ufoPath, "lib.plist")

# -----------------
# XML Normalization
# -----------------

# Property List

def normalizePropertyList(data, preprocessor=None):
    if preprocessor is not None:
        preprocessor(data)
    writer = XMLWriter(isPropertyList=True)
    writer.beginElement("plist", attrs=dict(version="1.0"))
    writer.propertyListObject(data)
    writer.endElement("plist")
    return writer.getText()

# GLIF

def normalizeGLIF(ufoPath, *subpath):
    """
    - Normalize the mark color if specified.

    TO DO: need doctests
    The best way to test this is going to be have a GLIF
    that contains all of the element types. This can be
    round tripped and compared to make sure that the result
    matches the expectations. This GLIF doesn't need to
    contain a robust series of element variations as the
    testing of those will be handled by the element
    normalization functions.

    >>> glifFormat = {}
    >>> glifFormat[1] = '''
    ... <?xml version="1.0" encoding="UTF-8"?>
    ... <glyph name="period" format="1">
    ...     <unicode hex="002E"/>
    ...     <advance width="268"/>
    ...     <outline>
    ...         <contour>
    ...             <point x="237" y="152"/>
    ...             <point x="193" y="187"/>
    ...             <point x="134" y="187" type="curve" smooth="yes"/>
    ...             <point x="74" y="187"/>
    ...             <point x="30" y="150"/>
    ...             <point x="30" y="88" type="curve" smooth="yes"/>
    ...             <point x="30" y="23"/>
    ...             <point x="74" y="-10"/>
    ...             <point x="134" y="-10" type="curve" smooth="yes"/>
    ...             <point x="193" y="-10"/>
    ...             <point x="237" y="25"/>
    ...             <point x="237" y="88" type="curve" smooth="yes"/>
    ...         </contour>
    ...         <component base="a"/>
    ...         <contour>
    ...             <point name="above" x="236" y="380" type="move"/>
    ...         </contour>
    ...     </outline>
    ...     <lib>
    ...         <dict>
    ...             <key>com.letterror.somestuff</key>
    ...             <string>arbitrary custom data!</string>
    ...         </dict>
    ...     </lib>
    ... </glyph>
    ... '''.strip().replace("    ", "\\t")

    >>> glifFormat[2] = '''
    ... <?xml version="1.0" encoding="UTF-8"?>
    ... <glyph name="period" format="2">
    ...     <unicode hex="002E"/>
    ...     <advance width="268"/>
    ...     <image fileName="period sketch.png" xScale="0.5" yScale="0.5"/>
    ...     <outline>
    ...         <contour>
    ...             <point name="above" x="236" y="380" type="move"/>
    ...         </contour>
    ...         <contour>
    ...             <point x="237" y="152"/>
    ...             <point x="193" y="187"/>
    ...             <point x="134" y="187" type="curve" smooth="yes"/>
    ...             <point x="74" y="187"/>
    ...             <point x="30" y="150"/>
    ...             <point x="30" y="88" type="curve" smooth="yes"/>
    ...             <point x="30" y="23"/>
    ...             <point x="74" y="-10"/>
    ...             <point x="134" y="-10" type="curve" smooth="yes"/>
    ...             <point x="193" y="-10"/>
    ...             <point x="237" y="25"/>
    ...             <point x="237" y="88" type="curve" smooth="yes"/>
    ...         </contour>
    ...         <component base="a"/>
    ...     </outline>
    ...     <anchor name="top" x="74" y="197"/>
    ...     <guideline name="overshoot" y="-12"/>
    ...     <lib>
    ...         <dict>
    ...             <key>com.letterror.somestuff</key>
    ...             <string>arbitrary custom data!</string>
    ...             <key>public.markColor</key>
    ...             <string>1,0,0,0.5</string>
    ...         </dict>
    ...     </lib>
    ...     <note>
    ...         arbitrary text about the glyph
    ...     </note>
    ... </glyph>
    ... '''.strip().replace("    ", "\\t")

    formats 1 & 2
    -------------
    >>> glifFolderPath = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data', 'glif')
    >>> os.chdir(glifFolderPath)
    >>> for i in [1, 2]:
    ...     glifFileName = 'format%s.glif' % i
    ...     normalizeGLIF(glifFolderPath, glifFileName)
    ...     glifFile = open(glifFileName, 'r')
    ...     glifFileData = glifFile.read()
    ...     glifFile.close()
    ...     glifFileData == glifFormat[i]
    True
    'period sketch.png'
    True

    no format
    ---------
    >>> glifFileName = 'formatNone.glif'
    >>> normalizeGLIF(glifFolderPath, glifFileName) # doctest: +ELLIPSIS
    Traceback (most recent call last):
        ...
    UFONormalizerError: Undefined GLIF format: ...formatNone.glif
    """
    # INVALID DATA POSSIBILITY: format version that can't be converted to int
    # read and parse
    glifPath = subpathJoin(ufoPath, *subpath)
    text = subpathReadFile(ufoPath, *subpath)
    tree = ET.fromstring(text)
    glifVersion = tree.attrib.get("format")
    if glifVersion is None:
        raise UFONormalizerError("Undefined GLIF format: %s" % glifPath)
    glifVersion = int(glifVersion)
    name = tree.attrib.get("name")
    # start the writer
    writer = XMLWriter()
    # grab the top-level elements
    advance = None
    unicodes = []
    note = None
    image = None
    guidelines = []
    anchors = []
    outline = None
    lib = None
    for element in tree:
        tag = element.tag
        if tag == "advance":
            advance = element
        elif tag == "unicode":
            unicodes.append(element)
        elif tag == "note":
            note = element
        elif tag == "image":
            image = element
        elif tag == "guideline":
            guidelines.append(element)
        elif tag == "anchor":
            anchors.append(element)
        elif tag == "outline":
            outline = element
        elif tag == "lib":
            lib = element
    imageFileName = None
    # write the data
    writer.beginElement("glyph", attrs=dict(name=name, format=glifVersion))
    for uni in unicodes:
        _normalizeGlifUnicode(uni, writer)
    if advance is not None:
        _normalizeGlifAdvance(advance, writer)
    if glifVersion >= 2 and image is not None:
        imageFileName = image.attrib.get("fileName")
        _normalizeGlifImage(image, writer)
    if outline is not None:
        if glifVersion == 1:
            _normalizeGlifOutlineFormat1(outline, writer)
        else:
            _normalizeGlifOutlineFormat2(outline, writer)
    if glifVersion >= 2:
        for anchor in anchors:
            _normalizeGlifAnchor(anchor, writer)
    if glifVersion >= 2:
        for guideline in guidelines:
            _normalizeGlifGuideline(guideline, writer)
    if lib is not None:
        _normalizeGlifLib(lib, writer)
    if note is not None:
        _normalizeGlifNote(note, writer)
    writer.endElement("glyph")
    # write to the file
    text = writer.getText()
    subpathWriteFile(text, ufoPath, *subpath)
    # return the image reference
    return imageFileName

def _normalizeGlifUnicode(element, writer):
    """
    - Don't write unicode element if hex attribute is not defined.
    - Don't write unicode element if value for hex value is not a proper hex value.
    - Write hex value as all uppercase, zero padded string.

    without hex
    -----------
    >>> element = ET.fromstring("<unicode />")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<unicode hex=''/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<unicode hexagon=''/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<unicode hex='xyz'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u''
    True

    with hex
    --------
    >>> element = ET.fromstring("<unicode hex='0041'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="0041"/>'
    True

    >>> element = ET.fromstring("<unicode hex='41'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="0041"/>'
    True

    >>> element = ET.fromstring("<unicode hex='ea'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="00EA"/>'
    True

    >>> element = ET.fromstring("<unicode hex='2Af'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="02AF"/>'
    True

    >>> element = ET.fromstring("<unicode hex='0000fFfF'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="FFFF"/>'
    True

    >>> element = ET.fromstring("<unicode hex='10000'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="10000"/>'
    True

    >>> element = ET.fromstring("<unicode hex='abcde'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifUnicode(element, writer)
    >>> writer.getText() == u'<unicode hex="ABCDE"/>'
    True
    """
    v = element.attrib.get("hex")
    # INVALID DATA POSSIBILITY: no hex value
    if v:
    # INVALID DATA POSSIBILITY: invalid hex value
        try:
            d = int(v, 16)
            v = "%04X" % d
        except ValueError:
            return
    else:
        return
    writer.simpleElement("unicode", attrs=dict(hex=v))

def _normalizeGlifAdvance(element, writer):
    """
    - Don't write default values (width=0, height=0)
    - Ignore values that can't be converted to a number.
    - Don't write an empty element.

    undefined
    ---------
    >>> element = ET.fromstring("<advance />")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    defaults
    --------
    >>> element = ET.fromstring("<advance width='0'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<advance height='0'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<advance width='0' height='0'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<advance width='1' height='0'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance width="1"/>'
    True

    >>> element = ET.fromstring('<advance width="0" height="1"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance height="1"/>'
    True

    width
    -----
    >>> element = ET.fromstring('<advance width="325.0"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance width="325"/>'
    True

    >>> element = ET.fromstring('<advance width="325.1"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance width="325.1"/>'
    True

    >>> element = ET.fromstring('<advance width="-325.0"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance width="-325"/>'
    True

    height
    ------
    >>> element = ET.fromstring('<advance height="325.0"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance height="325"/>'
    True

    >>> element = ET.fromstring('<advance height="325.1"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance height="325.1"/>'
    True

    >>> element = ET.fromstring('<advance height="-325.0"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u'<advance height="-325"/>'
    True

    invalid values
    --------------
    >>> element = ET.fromstring('<advance width="a" height="_"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring('<advance width="60" height="_"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring('<advance width="a" height="50"/>')
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAdvance(element, writer)
    >>> writer.getText() == u''
    True
    """
    # INVALID DATA POSSIBILITY: value that can't be converted to float
    w = element.attrib.get("width", "0")
    h = element.attrib.get("height", "0")
    try:
        w = float(w)
        h = float(h)
    except ValueError:
        return
    attrs = {}
    # filter out default value (0)
    if w:
        attrs["width"] = w
    if h:
        attrs["height"] = h
    if not attrs:
        return
    writer.simpleElement("advance", attrs=attrs)

def _normalizeGlifImage(element, writer):
    """
    - Don't write if fileName is not defined.

    everything
    ----------
    >>> element = ET.fromstring("<image fileName='Sketch 1.png' xOffset='100' yOffset='200' xScale='.75' yScale='.75' color='1,0,0,.5'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifImage(element, writer)
    >>> writer.getText() == u'<image fileName="Sketch 1.png" xScale="0.75" yScale="0.75" xOffset="100" yOffset="200" color="1,0,0,0.5"/>'
    True

    empty
    -----
    >>> element = ET.fromstring("<image />")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifImage(element, writer)
    >>> writer.getText() == u''
    True

    no file name
    ------------
    >>> element = ET.fromstring("<image xOffset='100' yOffset='200' xScale='.75' yScale='.75' color='1,0,0,.5'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifImage(element, writer)
    >>> writer.getText() == u''
    True

    no transformation
    -----------------
    >>> element = ET.fromstring("<image fileName='Sketch 1.png' color='1,0,0,.5' />")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifImage(element, writer)
    >>> writer.getText() == u'<image fileName="Sketch 1.png" color="1,0,0,0.5"/>'
    True

    no color
    --------
    >>> element = ET.fromstring("<image fileName='Sketch 1.png' xOffset='100' yOffset='200' xScale='.75' yScale='.75'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifImage(element, writer)
    >>> writer.getText() == u'<image fileName="Sketch 1.png" xScale="0.75" yScale="0.75" xOffset="100" yOffset="200"/>'
    True
    """
    # INVALID DATA POSSIBILITY: no file name defined
    # INVALID DATA POSSIBILITY: non-existent file referenced
    fileName = element.attrib.get("fileName")
    if not fileName:
        return
    attrs = dict(
        fileName=fileName
    )
    transformation = _normalizeGlifTransformation(element)
    attrs.update(transformation)
    color = element.attrib.get("color")
    if color is not None:
        attrs["color"] = _normalizeColorString(color)
    writer.simpleElement("image", attrs=attrs)

def _normalizeGlifAnchor(element, writer):
    """
    - Don't write if x or y are not defined.

    everything
    ----------
    >>> element = ET.fromstring("<anchor name='test' x='230' y='4.50' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u'<anchor name="test" x="230" y="4.5" color="1,0,0,0.5" identifier="TEST"/>'
    True

    no name
    -------
    >>> element = ET.fromstring("<anchor x='230' y='4.50' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u'<anchor x="230" y="4.5" color="1,0,0,0.5" identifier="TEST"/>'
    True

    no x
    ----
    >>> element = ET.fromstring("<anchor name='test' y='4.50' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<anchor name='test' x='invalid' y='4.50' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u''
    True

    no y
    ----
    >>> element = ET.fromstring("<anchor name='test' x='230' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<anchor name='test' x='230' y='invalid' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u''
    True

    no color
    --------
    >>> element = ET.fromstring("<anchor name='test' x='230' y='4.50' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u'<anchor name="test" x="230" y="4.5" identifier="TEST"/>'
    True

    no identifier
    -------------
    >>> element = ET.fromstring("<anchor name='test' x='230' y='4.50' color='1,0,0,.5'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifAnchor(element, writer)
    >>> writer.getText() == u'<anchor name="test" x="230" y="4.5" color="1,0,0,0.5"/>'
    True

    """
    # INVALID DATA POSSIBILITY: no x defined
    # INVALID DATA POSSIBILITY: no y defined
    # INVALID DATA POSSIBILITY: x or y that can't be converted to float
    x = element.attrib.get("x")
    y = element.attrib.get("y")
    # x or y undefined
    if not x or not y:
        return
    # x or y improperly defined
    try:
        x = float(x)
        y = float(y)
    except ValueError:
        return
    attrs = dict(
        x=x,
        y=y
    )
    name = element.attrib.get("name")
    if name is not None:
        attrs["name"] = name
    color = element.attrib.get("color")
    if color is not None:
        attrs["color"] = _normalizeColorString(color)
    identifier = element.attrib.get("identifier")
    if identifier is not None:
        attrs["identifier"] = identifier
    writer.simpleElement("anchor", attrs=attrs)

def _normalizeGlifGuideline(element, writer):
    """
    - Follow general guideline normalization rules.

    everything
    ----------
    >>> element = ET.fromstring("<guideline x='1' y='2' angle='3' name='test' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifGuideline(element, writer)
    >>> writer.getText() == u'<guideline name="test" x="1" y="2" angle="3" color="1,0,0,0.5" identifier="TEST"/>'
    True

    invalid
    -------
    >>> element = ET.fromstring("<guideline name='test' color='1,0,0,.5' identifier='TEST'/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifGuideline(element, writer)
    >>> writer.getText() == u''
    True
    """
    # INVALID DATA POSSIBILITY: x, y and angle not defined according to the spec
    # INVALID DATA POSSIBILITY: angle < 0 or > 360
    # INVALID DATA POSSIBILITY: x, y or angle that can't be converted to float
    attrs = "x y angle color name identifier".split(" ")
    converted = {}
    for attr in attrs:
        converted[attr] = element.attrib.get(attr)
    normalized = _normalizeDictGuideline(converted)
    if normalized is not None:
        writer.simpleElement("guideline", attrs=normalized)

def _normalizeGlifLib(element, writer):
    r"""
    - Don't write an empty element.

    defined
    -------
    >>> e = '''
    ... <lib>
    ...     <dict>
    ...         <key>foo</key>
    ...         <string>bar</string>
    ...     </dict>
    ... </lib>
    ... '''.strip()
    >>> element = ET.fromstring(e)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifLib(element, writer)
    >>> writer.getText() == u'<lib>\n\t<dict>\n\t\t<key>foo</key>\n\t\t<string>bar</string>\n\t</dict>\n</lib>'
    True

    undefined
    ---------
    >>> element = ET.fromstring("<lib></lib>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifLib(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<lib><dict></dict></lib>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifLib(element, writer)
    >>> writer.getText() == u''
    True
    """
    if not len(element):
        return
    obj = _convertPlistElementToObject(element[0])
    if obj:
        # normalize the mark color
        if "public.markColor" in obj:
            color = obj.pop("public.markColor")
            color = _normalizeColorString(color)
            if color is not None:
                obj["public.markColor"] = color
        writer.beginElement("lib")
        writer.propertyListObject(obj)
        writer.endElement("lib")

def _normalizeGlifNote(element, writer):
    r"""
    - Don't write an empty element.

    defined
    -------
    >>> element = ET.fromstring("<note>Blah</note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u"<note>\n\tBlah\n</note>"
    True

    >>> element = ET.fromstring("<note>   Blah  \t\n\t  </note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u"<note>\n\tBlah\n</note>"
    True

    >>> element = ET.fromstring("<note>Don't forget to check the béziers!!</note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u"<note>\n\tDon't forget to check the b\xe9ziers!!\n</note>"
    True

    >>> element = ET.fromstring("<note>A quick brown fox jumps over the lazy dog.\nPříliš žluťoučký kůň úpěl ďábelské ódy.</note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u"<note>\n\tA quick brown fox jumps over the lazy dog.\n\tP\u0159\xedli\u0161 \u017elu\u0165ou\u010dk\xfd k\u016f\u0148 \xfap\u011bl \u010f\xe1belsk\xe9 \xf3dy.\n</note>"
    True

    >>> element = ET.fromstring("<note>   Line1  \t\n\n    Line3\t  </note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u"<note>\n\tLine1\n\t\n\t    Line3\n</note>"
    True

    undefined
    ---------
    >>> element = ET.fromstring("<note></note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<note>   </note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<note>\n\n</note>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u''
    True

    >>> element = ET.fromstring("<note/>")
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifNote(element, writer)
    >>> writer.getText() == u''
    True
    """
    value = element.text
    if not value:
        return
    if not value.strip():
    	return
    writer.beginElement("note")
    writer.text(value)
    writer.endElement("note")

def _normalizeGlifOutlineFormat1(element, writer):
    r"""
    - Don't write an empty element.
    - Don't write an empty contour.
    - Don't write an empty component.
    - Retain contour and component order except for implied anchors in < UFO 3.
    - If the UFO format < 3, move implied anchors to the end.

    empty
    -----
    >>> outline = '''
    ... <outline/>
    ... '''
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat1(element, writer)
    >>> writer.getText() == u''
    True

    >>> outline = '''
    ... <outline>
    ... </outline>
    ... '''
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat1(element, writer)
    >>> writer.getText() == u''
    True

    >>> outline = '''
    ... <outline>
    ...     <contour/>
    ...     <component/>
    ... </outline>
    ... '''
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat1(element, writer)
    >>> writer.getText() == ''
    True

    element order
    -------------
    >>> outline = '''
    ... <outline>
    ...     <contour>
    ...         <point type="move" y="0" x="0" name="anchor1"/>
    ...     </contour>
    ...     <contour>
    ...         <point type="line" y="1" x="1"/>
    ...     </contour>
    ...     <component base="2"/>
    ...     <contour>
    ...         <point type="line" y="3" x="3"/>
    ...     </contour>
    ...     <component base="4"/>
    ...     <contour>
    ...         <point type="move" y="0" x="0" name="anchor2"/>
    ...     </contour>
    ... </outline>
    ... '''
    >>> expected = '''
    ... <outline>
    ...     <contour>
    ...         <point x="1" y="1" type="line"/>
    ...     </contour>
    ...     <component base="2"/>
    ...     <contour>
    ...         <point x="3" y="3" type="line"/>
    ...     </contour>
    ...     <component base="4"/>
    ...     <contour>
    ...         <point name="anchor1" x="0" y="0" type="move"/>
    ...     </contour>
    ...     <contour>
    ...         <point name="anchor2" x="0" y="0" type="move"/>
    ...     </contour>
    ... </outline>
    ... '''.strip().replace("    ", "\t")
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat1(element, writer)
    >>> writer.getText() == expected
    True
    """
    if not len(element):
        return
    outline = []
    anchors = []
    for subElement in element:
        tag = subElement.tag
        if tag == "contour":
            contour = _normalizeGlifContourFormat1(subElement)
            if contour is None:
                continue
            if contour["type"] == "contour":
                outline.append(contour)
            else:
                anchors.append(contour)
        elif tag == "component":
            component = _normalizeGlifComponentFormat1(subElement)
            if component is None:
                continue
            if component is not None:
                outline.append(component)
    if not outline and not anchors:
        return
    writer.beginElement("outline")
    for obj in outline:
        t = obj.pop("type")
        if t == "contour":
            writer.beginElement("contour")
            for point in obj["points"]:
                writer.simpleElement("point", attrs=point)
            writer.endElement("contour")
        elif t == "component":
            writer.simpleElement("component", attrs=obj)
    for anchor in anchors:
        t = anchor.pop("type")
        writer.beginElement("contour")
        attrs = dict(
            type="move",
            x=anchor["x"],
            y=anchor["y"]
        )
        if "name" in anchor:
            attrs["name"] = anchor["name"]
        writer.simpleElement("point", attrs=attrs)
        writer.endElement("contour")
    writer.endElement("outline")

def _normalizeGlifContourFormat1(element):
    r"""
    - Don't write unknown subelements.

    empty
    -----
    >>> contour = '''
    ... <contour/>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat1(element)

    >>> contour = '''
    ... <contour>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat1(element)

    point without attributes
    ------------------------
    >>> contour = '''
    ... <contour>
    ...    <point/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat1(element)

    unknown child element
    ---------------------
    >>> contour = '''
    ... <contour>
    ...    <piont type="line" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat1(element)

    unknown point type
    ------------------
    >>> contour = '''
    ... <contour>
    ...    <point type="invalid" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat1(element)

    implied anchor
    --------------
    >>> contour = '''
    ... <contour>
    ...    <point type="move" y="0" x="0" name="anchor1"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> sorted(_normalizeGlifContourFormat1(element).items())
    [('name', 'anchor1'), ('type', 'anchor'), ('x', 0.0), ('y', 0.0)]

    implied anchor with empty name
    ------------------------------
    >>> contour = '''
    ... <contour>
    ...    <point type="move" y="0" x="0" name=""/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> sorted(_normalizeGlifContourFormat1(element).items())
    [('name', ''), ('type', 'anchor'), ('x', 0.0), ('y', 0.0)]

    implied anchor without name
    ---------------------------
    >>> contour = '''
    ... <contour>
    ...    <point type="move" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> sorted(_normalizeGlifContourFormat1(element).items())
    [('type', 'anchor'), ('x', 0.0), ('y', 0.0)]

    normal
    ------
    >>> contour = '''
    ... <contour>
    ...    <point type="line" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> result = _normalizeGlifContourFormat1(element)
    >>> result["type"]
    'contour'
    >>> len(result["points"])
    1
    >>> sorted(result["points"][0].items())
    [('type', 'line'), ('x', 0.0), ('y', 0.0)]

    >>> contour = '''
    ... <contour>
    ...    <point type="move" y="0" x="0"/>
    ...    <point type="line" y="1" x="1"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> result = _normalizeGlifContourFormat1(element)
    >>> result["type"]
    'contour'
    >>> len(result["points"])
    2
    >>> sorted(result["points"][0].items())
    [('type', 'move'), ('x', 0.0), ('y', 0.0)]
    >>> sorted(result["points"][1].items())
    [('type', 'line'), ('x', 1.0), ('y', 1.0)]
    """
    # INVALID DATA POSSIBILITY: unknown child element
    # INVALID DATA POSSIBILITY: unknown point type
    points = []
    for subElement in element:
        tag = subElement.tag
        if tag != "point":
            continue
        attrs = _normalizeGlifPointAttributesFormat1(subElement)
        if not attrs:
            return
        points.append(attrs)
    if not points:
        return
    # anchor
    if len(points) == 1 and points[0].get("type") == "move":
        anchor = points[0]
        anchor["type"] = "anchor"
        return anchor
    # contour
    contour = dict(type="contour", points=points)
    return contour

def _normalizeGlifPointAttributesFormat1(element):
    """
    - Don't write if x or y is undefined.
    - Don't write default smooth value (no).
    - Don't write smooth for offcurves.
    - Don't write default point type attribute (offcurve).
    - Don't write subelements.
    - Don't write smooth if undefined.
    - Don't write unknown point types.

    everything
    ----------
    >>> point = "<point x='1' y='2.5' type='line' name='test' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('name', 'test'), ('smooth', 'yes'), ('type', 'line'), ('x', 1.0), ('y', 2.5)]

    no x
    ----
    >>> point = "<point y='2.5' type='line' name='test' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    []

    no y
    ----
    >>> point = "<point x='1' type='line' name='test' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    []

    invalid x
    ---------
    >>> point = "<point x='a' y='30'/>"
    >>> element = ET.fromstring(point)
    >>> _normalizeGlifPointAttributesFormat1(element)

    invalid y
    ---------
    >>> point = "<point x='20' y='b'/>"
    >>> element = ET.fromstring(point)
    >>> _normalizeGlifPointAttributesFormat1(element)

    no name
    -------
    >>> point = "<point x='1' y='2.5' type='line' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('smooth', 'yes'), ('type', 'line'), ('x', 1.0), ('y', 2.5)]

    empty name
    ----------
    >>> point = "<point x='1' y='2.5' type='line' name='' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('name', ''), ('smooth', 'yes'), ('type', 'line'), ('x', 1.0), ('y', 2.5)]

    type and smooth
    ---------------
    >>> point = "<point x='1' y='2.5' type='move' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('smooth', 'yes'), ('type', 'move'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='move' smooth='no'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'move'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='move'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'move'), ('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5' type='line' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('smooth', 'yes'), ('type', 'line'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='line' smooth='no'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'line'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='line'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'line'), ('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5' type='curve' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('smooth', 'yes'), ('type', 'curve'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='curve' smooth='no'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'curve'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='curve'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'curve'), ('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5' type='qcurve' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('smooth', 'yes'), ('type', 'qcurve'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='qcurve' smooth='no'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'qcurve'), ('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='qcurve'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('type', 'qcurve'), ('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5' type='offcurve' smooth='yes'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='offcurve' smooth='no'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('x', 1.0), ('y', 2.5)]
    >>> point = "<point x='1' y='2.5' type='offcurve'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('x', 1.0), ('y', 2.5)]

    >>> point = "<point x='1' y='2.5' type='invalid'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    []

    subelement
    ----------
    >>> point = "<point x='1' y='2.5'><invalid/></point>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat1(element).items())
    [('x', 1.0), ('y', 2.5)]
    """
    # INVALID DATA POSSIBILITY: no x defined
    # INVALID DATA POSSIBILITY: no y defined
    # INVALID DATA POSSIBILITY: x or y that can't be converted to float
    # INVALID DATA POSSIBILITY: duplicate attributes
    x = element.attrib.get("x")
    y = element.attrib.get("y")
    if not x or not y:
        return {}
    try:
        x = float(x)
        y = float(y)
    except ValueError:
        return
    attrs = dict(
        x=x,
        y=y
    )
    typ = element.attrib.get("type", "offcurve")
    if typ not in ("move", "line", "curve", "qcurve", "offcurve"):
        return {}
    if typ != "offcurve":
        attrs["type"] = typ
        smooth = element.attrib.get("smooth")
        if smooth == "yes":
            attrs["smooth"] = "yes"
    name = element.attrib.get("name")
    if name is not None:
        attrs["name"] = name
    return attrs

def _normalizeGlifComponentFormat1(element):
    """
    - Don't write if base is undefined.
    - Don't write subelements.

    everything
    ----------
    >>> component = "<component base='test' xScale='10' xyScale='2.2' yxScale='3' yScale='4.4' xOffset='5' yOffset='6.6'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentFormat1(element).items())
    [('base', 'test'), ('type', 'component'), ('xOffset', 5.0), ('xScale', 10.0), ('xyScale', 2.2), ('yOffset', 6.6), ('yScale', 4.4), ('yxScale', 3.0)]

    no base
    -------
    >>> component = "<component xScale='1' xyScale='2.2' yxScale='3' yScale='4.4' xOffset='5' yOffset='6.6'/>"
    >>> element = ET.fromstring(component)
    >>> _normalizeGlifComponentFormat1(element)

    subelement
    ----------
    >>> component = "<component base='test'><foo/></component>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentFormat1(element).items())
    [('base', 'test'), ('type', 'component')]
    """
    # INVALID DATA POSSIBILITY: no base defined
    # INVALID DATA POSSIBILITY: unknown child element
    component = _normalizeGlifComponentAttributesFormat1(element)
    if not component:
        return
    component["type"] = "component"
    return component

def _normalizeGlifComponentAttributesFormat1(element):
    """
    - Don't write if base is not defined.
    - Don't write default transformation values.

    everything
    ----------
    >>> component = "<component base='test' xScale='10' xyScale='2.2' yxScale='3' yScale='4.4' xOffset='5' yOffset='6.6'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentAttributesFormat1(element).items())
    [('base', 'test'), ('xOffset', 5.0), ('xScale', 10.0), ('xyScale', 2.2), ('yOffset', 6.6), ('yScale', 4.4), ('yxScale', 3.0)]

    no base
    -------
    >>> component = "<component xScale='10' xyScale='2.2' yxScale='3' yScale='4.4' xOffset='5' yOffset='6.6'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentAttributesFormat1(element).items())
    []

    no transformation
    -----------------
    >>> component = "<component base='test'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentAttributesFormat1(element).items())
    [('base', 'test')]

    defaults
    --------
    >>> component = "<component base='test' xScale='1' xyScale='0' yxScale='0' yScale='1' xOffset='0' yOffset='0'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentAttributesFormat1(element).items())
    [('base', 'test')]
    """
    # INVALID DATA POSSIBILITY: no base defined
    # INVALID DATA POSSIBILITY: duplicate attributes
    base = element.attrib.get("base")
    if not base:
        return {}
    attrs = dict(
        base=element.attrib["base"]
    )
    transformation = _normalizeGlifTransformation(element)
    attrs.update(transformation)
    return attrs

def _normalizeGlifOutlineFormat2(element, writer):
    r"""
    - Don't write an empty element.
    - Don't write an empty contour.
    - Don't write an empty component.
    - Retain contour and component order.
    - Don't write unknown subelements.

    empty
    -----
    >>> outline = '''
    ... <outline>
    ... </outline>
    ... '''
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat2(element, writer)
    >>> writer.getText() == u''
    True

    >>> outline = '''
    ... <outline>
    ...     <contour />
    ...     <component />
    ... </outline>
    ... '''
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat2(element, writer)
    >>> writer.getText() == u''
    True

    element order
    -------------
    >>> outline = '''
    ... <outline>
    ...     <contour>
    ...         <point type="line" y="1" x="1"/>
    ...     </contour>
    ...     <component base="2"/>
    ...     <contour identifier='test'>
    ...         <point type="line" y="3" x="3"/>
    ...     </contour>
    ...     <component base="4"/>
    ... </outline>
    ... '''
    >>> expected = '''
    ... <outline>
    ...     <contour>
    ...         <point x="1" y="1" type="line"/>
    ...     </contour>
    ...     <component base="2"/>
    ...     <contour identifier="test">
    ...         <point x="3" y="3" type="line"/>
    ...     </contour>
    ...     <component base="4"/>
    ... </outline>
    ... '''.strip().replace("    ", "\t")
    >>> element = ET.fromstring(outline)
    >>> writer = XMLWriter(declaration=None)
    >>> _normalizeGlifOutlineFormat2(element, writer)
    >>> writer.getText() == expected
    True
    """
    outline = []
    for subElement in element:
        tag = subElement.tag
        if tag == "contour":
            contour = _normalizeGlifContourFormat2(subElement)
            if contour:
                outline.append(contour)
        elif tag == "component":
            component = _normalizeGlifComponentFormat2(subElement)
            if component:
                outline.append(component)
    if not outline:
        return
    writer.beginElement("outline")
    for obj in outline:
        t = obj.pop("type")
        if t == "contour":
            attrs = {}
            identifier = obj.get("identifier")
            if identifier is not None:
                attrs["identifier"] = identifier
            writer.beginElement("contour", attrs=attrs)
            for point in obj["points"]:
                writer.simpleElement("point", attrs=point)
            writer.endElement("contour")
        elif t == "component":
            writer.simpleElement("component", attrs=obj)
    writer.endElement("outline")

def _normalizeGlifContourFormat2(element):
    r"""
    - Don't write unknown subelements.

    empty
    -----
    >>> contour = '''
    ... <contour>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat2(element)

    point without attributes
    ------------------------
    >>> contour = '''
    ... <contour>
    ...    <point/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat2(element)

    unknown child element
    ---------------------
    >>> contour = '''
    ... <contour>
    ...    <piont type="line" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> _normalizeGlifContourFormat2(element)

    normal
    ------
    >>> contour = '''
    ... <contour identifier="test">
    ...    <point type="line" y="0" x="0"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> result = _normalizeGlifContourFormat2(element)
    >>> result["type"]
    'contour'
    >>> result["identifier"]
    'test'
    >>> len(result["points"])
    1
    >>> sorted(result["points"][0].items())
    [('type', 'line'), ('x', 0.0), ('y', 0.0)]

    >>> contour = '''
    ... <contour identifier="test">
    ...    <point type="move" y="0" x="0"/>
    ...    <point type="line" y="1" x="1"/>
    ... </contour>
    ... '''
    >>> element = ET.fromstring(contour)
    >>> result = _normalizeGlifContourFormat2(element)
    >>> result["type"]
    'contour'
    >>> result["identifier"]
    'test'
    >>> len(result["points"])
    2
    >>> sorted(result["points"][0].items())
    [('type', 'move'), ('x', 0.0), ('y', 0.0)]
    >>> sorted(result["points"][1].items())
    [('type', 'line'), ('x', 1.0), ('y', 1.0)]
    """
    # INVALID DATA POSSIBILITY: unknown child element
    # INVALID DATA POSSIBILITY: unknown point type
    points = []
    for subElement in element:
        tag = subElement.tag
        if tag != "point":
            continue
        attrs = _normalizeGlifPointAttributesFormat2(subElement)
        if not attrs:
            return
        points.append(attrs)
    if not points:
        return
    contour = dict(type="contour", points=points)
    identifier = element.attrib.get("identifier")
    if identifier is not None:
        contour["identifier"] = identifier
    return contour

def _normalizeGlifPointAttributesFormat2(element):
    """
    - Follow same rules as Format 1, but allow an identifier attribute.

    everything
    ----------
    >>> point = "<point x='1' y='2.5' type='line' name='test' smooth='yes' identifier='TEST'/>"
    >>> element = ET.fromstring(point)
    >>> sorted(_normalizeGlifPointAttributesFormat2(element).items())
    [('identifier', 'TEST'), ('name', 'test'), ('smooth', 'yes'), ('type', 'line'), ('x', 1.0), ('y', 2.5)]
    """
    attrs = _normalizeGlifPointAttributesFormat1(element)
    identifier = element.attrib.get("identifier")
    if identifier is not None:
        attrs["identifier"] = identifier
    return attrs

def _normalizeGlifComponentFormat2(element):
    """
    - Folow the same rules as Format 1.
    """
    # INVALID DATA POSSIBILITY: no base defined
    # INVALID DATA POSSIBILITY: unknown child element
    component = _normalizeGlifComponentAttributesFormat2(element)
    if not component:
        return
    component["type"] = "component"
    return component

def _normalizeGlifComponentAttributesFormat2(element):
    """
    - Follow same rules as Format 1, but allow an identifier attribute.

    everything
    ----------
    >>> component = "<component base='test' xScale='10' xyScale='2.2' yxScale='3' yScale='4.4' xOffset='5' yOffset='6.6' identifier='test'/>"
    >>> element = ET.fromstring(component)
    >>> sorted(_normalizeGlifComponentAttributesFormat2(element).items())
    [('base', 'test'), ('identifier', 'test'), ('xOffset', 5.0), ('xScale', 10.0), ('xyScale', 2.2), ('yOffset', 6.6), ('yScale', 4.4), ('yxScale', 3.0)]
    """
    attrs = _normalizeGlifComponentAttributesFormat1(element)
    identifier = element.attrib.get("identifier")
    if identifier is not None:
        attrs["identifier"] = identifier
    return attrs

_glifDefaultTransformation = dict(
    xScale=1,
    xyScale=0,
    yxScale=0,
    yScale=1,
    xOffset=0,
    yOffset=0
)

def _normalizeGlifTransformation(element):
    """
    - Don't write default values.

    empty
    -----
    >>> element = ET.fromstring("<test/>")
    >>> _normalizeGlifTransformation(element)
    {}

    default
    -------
    >>> element = ET.fromstring("<test xScale='1' xyScale='0' yxScale='0' yScale='1' xOffset='0' yOffset='0'/>")
    >>> _normalizeGlifTransformation(element)
    {}

    non-default
    -----------
    >>> element = ET.fromstring("<test xScale='2' xyScale='3' yxScale='4' yScale='5' xOffset='6' yOffset='7'/>")
    >>> sorted(_normalizeGlifTransformation(element).items())
    [('xOffset', 6.0), ('xScale', 2.0), ('xyScale', 3.0), ('yOffset', 7.0), ('yScale', 5.0), ('yxScale', 4.0)]

    invalid value
    -------------
    >>> element = ET.fromstring("<test xScale='a'/>")
    >>> _normalizeGlifTransformation(element)
    {}

    unknown attribute
    -----------------
    >>> element = ET.fromstring("<test rotate='1'/>")
    >>> _normalizeGlifTransformation(element)
    {}
    """
    attrs = {}
    for attr, default in _glifDefaultTransformation.items():
        value = element.attrib.get(attr, default)
        try:
            value = float(value)
        except ValueError:
            continue
        if value != default:
            attrs[attr] = value
    return attrs

def _normalizeColorString(value):
    """
    - Write the string as comma separated numbers, folowing the
      number normalization rules.

    >>> _normalizeColorString("")
    >>> _normalizeColorString("1,1,1")
    >>> _normalizeColorString("1,1,1,1")
    '1,1,1,1'
    >>> _normalizeColorString(".1,.1,.1,.1")
    '0.1,0.1,0.1,0.1'
    >>> _normalizeColorString("1,1,1,a")
    >>> _normalizeColorString("1,1,-1,1")
    >>> _normalizeColorString("1,2,1,1")
    >>> _normalizeColorString(",,,")
    """
    # INVALID DATA POSSIBILITY: bad color string
    # INVALID DATA POSSIBILITY: value < 0 or > 1
    if value.count(",") != 3:
        return
    try:
        r, g, b, a = (float(i) for i in value.split(","))
    except ValueError:
        return
    if any(x < 0 or x > 1 for x in (r, g, b, a)):
    	return
    color = (xmlConvertFloat(i) for i in (r, g, b, a))
    return ",".join(color)

# Adapted from plistlib.datetime._date_from_string()
def _dateFromString(text):
    import re
    _dateParser = re.compile(r"(?P<year>\d\d\d\d)(?:-(?P<month>\d\d)(?:-(?P<day>\d\d)(?:T(?P<hour>\d\d)(?::(?P<minute>\d\d)(?::(?P<second>\d\d))?)?)?)?)?Z")
    gd = _dateParser.match(text).groupdict()
    lst = []
    for key in ('year', 'month', 'day', 'hour', 'minute', 'second'):
        val = gd[key]
        if val is None:
            break
        lst.append(int(val))
    return datetime.datetime(*lst)

def _dateToString(data):
    return '%04d-%02d-%02dT%02d:%02d:%02dZ' % (data.year, data.month,
                                               data.day, data.hour,
                                               data.minute, data.second)

def _convertPlistElementToObject(element):
    """
    >>> element = ET.fromstring("<array></array>")
    >>> _convertPlistElementToObject(element)
    []
    >>> element = ET.fromstring("<array><integer>0</integer><real>.1</real></array>")
    >>> _convertPlistElementToObject(element)
    [0, 0.1]
    >>> element = ET.fromstring("<dict></dict>")
    >>> _convertPlistElementToObject(element)
    {}
    >>> element = ET.fromstring("<dict><key>foo</key><string>bar</string></dict>")
    >>> _convertPlistElementToObject(element)
    {'foo': 'bar'}
    >>> element = ET.fromstring("<string>foo</string>")
    >>> _convertPlistElementToObject(element)
    'foo'
    >>> element = ET.fromstring("<date>2015-07-05T22:16:18Z</date>")
    >>> _convertPlistElementToObject(element)
    datetime.datetime(2015, 7, 5, 22, 16, 18)
    >>> element = ET.fromstring("<true />")
    >>> _convertPlistElementToObject(element)
    True
    >>> element = ET.fromstring("<false />")
    >>> _convertPlistElementToObject(element)
    False
    >>> element = ET.fromstring("<real>1.1</real>")
    >>> _convertPlistElementToObject(element)
    1.1
    >>> element = ET.fromstring("<integer>1</integer>")
    >>> _convertPlistElementToObject(element)
    1
    >>> element = ET.fromstring("<data>YWJj</data>")
    >>> _convertPlistElementToObject(element) == plistlib.Data(b'abc')
    True
    """
    # INVALID DATA POSSIBILITY: invalid value string
    obj = None
    tag = element.tag
    if tag == "array":
        obj = []
        for subElement in element:
            obj.append(_convertPlistElementToObject(subElement))
    elif tag == "dict":
        obj = {}
        key = None
        for subElement in element:
            if subElement.tag == "key":
                key = subElement.text
            else:
                obj[key] = _convertPlistElementToObject(subElement)
    elif tag == "string":
        return element.text
    elif tag == "data":
        return plistlib.Data.fromBase64(element.text)
    elif tag == "date":
        return _dateFromString(element.text)
    elif tag == "true":
        return True
    elif tag == "false":
        return False
    elif tag == "real":
        return float(element.text)
    elif tag == "integer":
        return int(element.text)
    return obj

# XML Writer

xmlDeclaration = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
plistDocType = "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
xmlTextMaxLineLength = 70
xmlIndent = "\t"
xmlLineBreak = "\n"
xmlAttributeOrder = """
name
base
format
fileName
x
y
angle
xScale
xyScale
yxScale
yScale
xOffset
yOffset
type
smooth
color
identifier
""".strip().splitlines()
d = {}
for index, attr in enumerate(xmlAttributeOrder):
    d[attr] = index
xmlAttributeOrder = d

class XMLWriter(object):

    def __init__(self, isPropertyList=False, declaration=xmlDeclaration):
        self._lines = []
        if declaration:
            self._lines.append(declaration)
        if isPropertyList:
            self._lines.append(plistDocType)
        self._indentLevel = 0
        self._stack = []

    # text retrieval

    def getText(self):
        assert not self._stack
        return xmlLineBreak.join(self._lines)

    # writing

    def raw(self, line):
        if self._indentLevel:
            i = xmlIndent * self._indentLevel
            line = i + line
        self._lines.append(line)

    def data(self, text):
        line = "<![CDATA[%s]]>" % text
        self.raw(line)

    def text(self, text):
        text = text.strip()
        text = xmlEscapeText(text)
        paragraphs = []
        for paragraph in text.splitlines():
            if not paragraph:
                paragraphs.append("")
            else:
                paragraph = textwrap.wrap(paragraph.rstrip(),
                    width=xmlTextMaxLineLength,
                    expand_tabs=False,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=False,
                    break_on_hyphens=False
                )
                paragraphs.extend(paragraph)
        for line in paragraphs:
            self.raw(line)

    def simpleElement(self, tag, attrs={}, value=None):
        if attrs:
            attrs = self.attributesToString(attrs)
            line = "<%s %s" % (tag, attrs)
        else:
            line = "<%s" % tag
        if value is not None:
            line = "%s>%s</%s>" % (line, value, tag)
        else:
            line = "%s/>" % line
        self.raw(line)

    def beginElement(self, tag, attrs={}):
        if attrs:
            attrs = self.attributesToString(attrs)
            line = "<%s %s>" % (tag, attrs)
        else:
            line = "<%s>" % tag
        self.raw(line)
        self._stack.append(tag)
        self._indentLevel += 1

    def endElement(self, tag):
        assert self._stack
        assert self._stack[-1] == tag
        del self._stack[-1]
        self._indentLevel -= 1
        line = "</%s>" % (tag)
        self.raw(line)

    # property list

    def propertyListObject(self, data):
        """
        Array
        -----
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject([])
        >>> writer.getText() == u'<array>\\n</array>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(["a"])
        >>> writer.getText() == u'<array>\\n\\t<string>a</string>\\n</array>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject([None])
        >>> writer.getText() == u'<array>\\n</array>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject([False])
        >>> writer.getText() == u'<array>\\n\\t<false/>\\n</array>'
        True

        Dict
        ----
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({})
        >>> writer.getText() == u'<dict>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({"a" : "b"})
        >>> writer.getText() == u'<dict>\\n\\t<key>a</key>\\n\\t<string>b</string>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({"a" : 20.0})
        >>> writer.getText() == u'<dict>\\n\\t<key>a</key>\\n\\t<real>20</real>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({"" : ""})
        >>> writer.getText() == u'<dict>\\n\\t<key></key>\\n\\t<string></string>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({None : ""})
        >>> writer.getText() == u'<dict>\\n\\t<key/>\\n\\t<string></string>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({"" : None})
        >>> writer.getText() == u'<dict>\\n\\t<key></key>\\n</dict>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject({None : None})
        >>> writer.getText() == u'<dict>\\n\\t<key/>\\n</dict>'
        True

        String
        ------
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject("a")
        >>> writer.getText() == u'<string>a</string>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject("1.000")
        >>> writer.getText() == u'<string>1.000</string>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject("")
        >>> writer.getText() == u'<string></string>'
        True

        Boolean
        -------
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(True)
        >>> writer.getText() == u'<true/>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(False)
        >>> writer.getText() == u'<false/>'
        True

        Float
        -----
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(1.1)
        >>> writer.getText() == u'<real>1.1</real>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(-1.1)
        >>> writer.getText() == u'<real>-1.1</real>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(1.0)
        >>> writer.getText() == u'<real>1</real>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(-1.0)
        >>> writer.getText() == u'<real>-1</real>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(0.0)
        >>> writer.getText() == u'<real>0</real>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(-0.0)
        >>> writer.getText() == u'<real>0</real>'
        True

        Integer
        -------
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(1)
        >>> writer.getText() == u'<integer>1</integer>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(-1)
        >>> writer.getText() == u'<integer>-1</integer>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(+1)
        >>> writer.getText() == u'<integer>1</integer>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(0)
        >>> writer.getText() == u'<integer>0</integer>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(-0)
        >>> writer.getText() == u'<integer>0</integer>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(2015-1-1)
        >>> writer.getText() == u'<integer>2013</integer>'
        True

        Date
        ----
        >>> writer = XMLWriter(declaration=None)
        >>> date = datetime.datetime(2012, 9, 1)
        >>> writer.propertyListObject(date)
        >>> writer.getText() == u'<date>2012-09-01T00:00:00Z</date>'
        True

        >>> writer = XMLWriter(declaration=None)
        >>> date = datetime.datetime(2009, 11, 29, 16, 31, 53)
        >>> writer.propertyListObject(date)
        >>> writer.getText() == u'<date>2009-11-29T16:31:53Z</date>'
        True

        Data
        ----
        >>> writer = XMLWriter(declaration=None)
        >>> data = plistlib.Data(tobytes("abc"))
        >>> writer.propertyListObject(data)
        >>> writer.getText() == u'<data>\\n\\tYWJj\\n</data>'
        True

        None
        ----
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(None)
        >>> writer.getText() == u''
        True

        Unknown data type
        -----------------
        >>> writer = XMLWriter(declaration=None)
        >>> writer.propertyListObject(1.0j) # doctest: +ELLIPSIS
        Traceback (most recent call last):
            ...
        UFONormalizerError: Unknown data type in property list: <... 'complex'>
        """
        if data is None:
            return
        if isinstance(data, (list, tuple)):
            self._plistArray(data)
        elif isinstance(data, dict):
            self._plistDict(data)
        elif isinstance(data, basestring):
            self._plistString(data)
        elif isinstance(data, bool):
            self._plistBoolean(data)
        elif isinstance(data, (int, long)):
            self._plistInt(data)
        elif isinstance(data, float):
            self._plistFloat(data)
        elif isinstance(data, plistlib.Data):
            self._plistData(data)
        elif isinstance(data, datetime.datetime):
            self._plistDate(data)
        else:
            raise UFONormalizerError("Unknown data type in property list: %s" % repr(type(data)))

    def _plistArray(self, data):
        self.beginElement("array")
        for value in data:
            self.propertyListObject(value)
        self.endElement("array")

    def _plistDict(self, data):
        self.beginElement("dict")
        for key, value in sorted(data.items()):
            self.simpleElement("key", value=key)
            self.propertyListObject(value)
        self.endElement("dict")

    def _plistString(self, data):
        self.simpleElement("string", value=data)

    def _plistBoolean(self, data):
        if data:
            self.simpleElement("true")
        else:
            self.simpleElement("false")

    def _plistFloat(self, data):
        data = xmlConvertFloat(data)
        self.simpleElement("real", value=data)

    def _plistInt(self, data):
        data = xmlConvertInt(data)
        self.simpleElement("integer", value=data)

    def _plistDate(self, data):
        data = _dateToString(data)
        self.simpleElement("date", value=data)

    def _plistData(self, data):
        self.beginElement("data")
        data = data.asBase64(maxlinelength=xmlTextMaxLineLength)
        for line in tostr(data).splitlines():
            self.raw(line)
        self.endElement("data")

    # support

    def attributesToString(self, attrs):
        """
        - Sort the known attributes in the preferred order.
        - Sort unknown attributes in alphabetical order and
          place them after the known attributes.
        - Format as space separated name="value".

        >>> attrs = dict(a="blah", x=1, y=2.1)
        >>> writer = XMLWriter(declaration=None)
        >>> writer.attributesToString(attrs)
        'x="1" y="2.1" a="blah"'
        """
        sorter = [
            (xmlAttributeOrder.get(attr, 100), attr, value) for (attr, value) in attrs.items()
        ]
        formatted = []
        for index, attr, value in sorted(sorter):
            attr = xmlEscapeAttribute(attr)
            value = xmlConvertValue(value)
            pair = "%s=\"%s\"" % (attr, value)
            formatted.append(pair)
        return " ".join(formatted)


def xmlEscapeText(text):
    r"""
    NOTE: In Python 2.x, the doctest module is not robust enough to deal with non-ASCII
          characters; to make the tests work, the doctest string needs to be raw,
          and the results need to be escaped hexadecimal values of each byte.
          In Python 3.x all strings are Unicode-encoded by default, which allows for
          the doctests results to use any Unicode character.

    >>> xmlEscapeText(u"&") == u"&amp;"
    True
    >>> xmlEscapeText(u"<") == u"&lt;"
    True
    >>> xmlEscapeText(u">") == u"&gt;"
    True
    >>> xmlEscapeText(u"a") == u"a"
    True
    >>> xmlEscapeText(u"ä") == u"ä"
    True
    >>> xmlEscapeText(u"ā") == u"ā"
    True
    >>> xmlEscapeText(u"𐐀") == u"𐐀"
    True
    >>> xmlEscapeText(u"©") == u"©"
    True
    >>> xmlEscapeText(u"—") == u"—"
    True
    >>> xmlEscapeText(u"1") == u"1"
    True
    >>> xmlEscapeText(u"1.0") == u"1.0"
    True
    >>> xmlEscapeText(u"'") == u"'"
    True
    >>> xmlEscapeText(u"/") == u"/"
    True
    >>> xmlEscapeText(u"\\") == u"\\"
    True
    >>> xmlEscapeText(u"\\r") == u"\\r"
    True
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def xmlEscapeAttribute(text):
    r"""
    >>> xmlEscapeAttribute('"')
    '&quot;'
    >>> xmlEscapeAttribute("'")
    "'"
    >>> xmlEscapeAttribute("abc")
    'abc'
    >>> xmlEscapeAttribute("123")
    '123'
    >>> xmlEscapeAttribute("/")
    '/'
    >>> xmlEscapeAttribute("\\")
    '\\'
    """
    text = xmlEscapeText(text)
    text = text.replace("\"", "&quot;")
    return text

def xmlConvertValue(value):
    """
    >>> xmlConvertValue(0.0)
    '0'
    >>> xmlConvertValue(-0.0)
    '0'
    >>> xmlConvertValue(2.0)
    '2'
    >>> xmlConvertValue(-2.0)
    '-2'
    >>> xmlConvertValue(2.05)
    '2.05'
    >>> xmlConvertValue(2)
    '2'
    >>> xmlConvertValue(0.2)
    '0.2'
    >>> xmlConvertValue("0.0")
    '0.0'
    >>> xmlConvertValue(1e-5)
    '0.00001'
    >>> xmlConvertValue(1e-10)
    '0.0000000001'
    >>> xmlConvertValue(1e-11)
    '0'
    >>> xmlConvertValue(1e+5)
    '100000'
    >>> xmlConvertValue(1e+10)
    '10000000000'
    """
    if isinstance(value, float):
        return xmlConvertFloat(value)
    elif isinstance(value, int):
        return xmlConvertInt(value)
    value = xmlEscapeText(value)
    return value

def xmlConvertFloat(value):
    """
    >>> xmlConvertFloat(1.0)
    '1'
    >>> xmlConvertFloat(1.01)
    '1.01'
    >>> xmlConvertFloat(1.0000000001)
    '1.0000000001'
    >>> xmlConvertFloat(1.00000000001)
    '1'
    >>> xmlConvertFloat(1.00000000009)
    '1.0000000001'
    """
    value = "%.10f" % value
    value = value.rstrip("0")
    if value[-1] == ".":
        return xmlConvertInt(int(float(value)))
    return value

def xmlConvertInt(value):
    """
    >>> xmlConvertInt(1)
    '1'
    >>> xmlConvertInt(-1)
    '-1'
    >>> xmlConvertInt(- 1)
    '-1'
    >>> xmlConvertInt(0)
    '0'
    >>> xmlConvertInt(-0)
    '0'
    >>> xmlConvertInt(0o01)
    '1'
    >>> xmlConvertInt(- 0o01)
    '-1'
    >>> xmlConvertInt(0o000001)
    '1'
    >>> xmlConvertInt(0o0000000000000001)
    '1'
    >>> xmlConvertInt(1000000000000001)
    '1000000000000001'
    >>> xmlConvertInt(0o000001000001)
    '262145'
    >>> xmlConvertInt(0o00000100000)
    '32768'
    >>> xmlConvertInt(0o0000010)
    '8'
    >>> xmlConvertInt(-0o0000010)
    '-8'
    >>> xmlConvertInt(0o0000020)
    '16'
    >>> xmlConvertInt(0o0000030)
    '24'
    >>> xmlConvertInt(65536)
    '65536'
    """
    return str(value)

# ---------------
# Path Operations
# ---------------

def duplicateUFO(inPath, outPath):
    """
    Duplicate an entire UFO.
    """
    if os.path.exists(outPath):
        shutil.rmtree(outPath)
    shutil.copytree(inPath, outPath)

def subpathJoin(ufoPath, *subpath):
    """
    Join path parts.
    """
    if not isinstance(subpath, basestring):
        subpath = os.path.join(*subpath)
    return os.path.join(ufoPath, subpath)

def subpathSplit(path):
    """
    Split path parts.
    """
    return os.path.split(path)

def subpathExists(ufoPath, *subpath):
    """
    Get a boolean indicating if a path exists.
    """
    path = subpathJoin(ufoPath, *subpath)
    return os.path.exists(path)

# read

def subpathReadFile(ufoPath, *subpath):
    """
    Read the contents of a file.
    """
    path = subpathJoin(ufoPath, *subpath)
    f = open(path, "rb")
    text = f.read()
    f.close()
    return text

def subpathReadPlist(ufoPath, *subpath):
    """
    Read the contents of a property list
    and convert it into a Python object.
    """
    text = subpathReadFile(ufoPath, *subpath)
    return _readPlistFromBytes(text)

# write

def subpathWriteFile(data, ufoPath, *subpath):
    """
    Write data to a file.

    This will only modify the file if the
    file contains data that is different
    from the new data.
    """
    path = subpathJoin(ufoPath, *subpath)
    if subpathExists(ufoPath, *subpath):
        existing = subpathReadFile(ufoPath, *subpath)
    else:
        existing = None
    if data != existing:
        f = open(path, "wb")
        f.write(tobytes(data))
        f.close()

def subpathWritePlist(data, ufoPath, *subpath):
    """
    Write a Python object to a property list.
    THIS DOES NOT WRITE NORMALIZED OUTPUT.

    This will only modify the file if the
    file contains data that is different
    from the new data.
    """
    data = _writePlistToBytes(data)
    subpathWriteFile(data, ufoPath, *subpath)

# rename

def subpathRenameFile(ufoPath, fromSubpath, toSubpath):
    """
    Rename a file.
    """
    if isinstance(fromSubpath, basestring):
        fromSubpath = [fromSubpath]
    if isinstance(toSubpath, basestring):
        toSubpath = [toSubpath]
    inPath = subpathJoin(ufoPath, *fromSubpath)
    outPath = subpathJoin(ufoPath, *toSubpath)
    os.rename(inPath, outPath)

def subpathRenameDirectory(ufoPath, fromSubpath, toSubpath):
    """
    Rename a directory.
    """
    if isinstance(fromSubpath, basestring):
        fromSubpath = [fromSubpath]
    if isinstance(toSubpath, basestring):
        toSubpath = [toSubpath]
    inPath = subpathJoin(ufoPath, *fromSubpath)
    outPath = subpathJoin(ufoPath, *toSubpath)
    shutil.move(inPath, outPath)

# remove

def subpathRemoveFile(ufoPath, *subpath):
    """
    Remove a file.
    """
    if subpathExists(ufoPath, *subpath):
        path = subpathJoin(ufoPath, *subpath)
        os.remove(path)

# mod times

def subpathGetModTime(ufoPath, *subpath):
    """
    Get the modification time for a file.
    """
    path = subpathJoin(ufoPath, *subpath)
    return os.path.getmtime(path)

def subpathNeedsRefresh(modTimes, ufoPath, *subPath):
    """
    Determine if a file needs to be refreshed.
    Returns True if the file's latest modification time is different
    from its previous modification time.
    """
    previous = modTimes.get(subPath[-1])
    if previous is None:
        return True
    latest = subpathGetModTime(ufoPath, *subPath)
    return latest != previous

# ---------------
# Store Mod Times
# ---------------

def storeModTimes(lib, modTimes):
    """
    Write the file mod times to the lib.
    """
    lines = [
        "version: %s" % __version__
    ]
    for fileName, modTime in sorted(modTimes.items()):
        line = "%.1f %s" % (modTime, fileName)
        lines.append(line)
    text = "\n".join(lines)
    lib[modTimeLibKey] = text

def readModTimes(lib):
    """
    Read the file mod times from the lib.
    """
    # TO DO: a version mismatch causing a complete
    # renomalization of existing files sucks. but,
    # I haven't been able to come up with a better
    # solution. maybe we could keep track of what
    # would need new normalization from version to
    # version and only trigger it as needed. most
    # new versions aren't going to require a complete
    # rerun of everything.
    text = lib.get(modTimeLibKey)
    if not text:
        return {}
    lines = text.splitlines()
    version = lines.pop(0).split(":")[-1].strip()
    if version != __version__:
        return {}
    modTimes = {}
    for line in lines:
        modTime, fileName = line.split(" ", 1)
        modTime = float(modTime)
        modTimes[fileName] = modTime
    return modTimes

# ----------------
# Image Management
# ----------------

def readImagesDirectory(ufoPath):
    """
    Get a listing of all images in the images directory.
    """
    pattern = subpathJoin(ufoPath, *["images", "*.png"])
    imageNames = [subpathSplit(path)[-1] for path in glob.glob(pattern)]
    return set(imageNames)

def purgeImagesDirectory(ufoPath, toPurge):
    """
    Purge specified images from the images directory.
    """
    for fileName in toPurge:
        if subpathExists(ufoPath, *["images", fileName]):
            path = subpathJoin(ufoPath, *["images", fileName])
            os.remove(path)

def storeImageReferences(lib, imageReferences):
    """
    Store the image references.
    """
    lib[imageReferencesLibKey] = imageReferences

def readImageReferences(lib):
    """
    Read the image references.
    """
    references = lib.get(imageReferencesLibKey)
    return references

# ----------------------
# User Name to File Name
# ----------------------
#
# This was taken directly from the UFO 3 specification.

illegalCharacters = "\" * + / : < > ? [ \ ] | \0".split(" ")
illegalCharacters += [chr(i) for i in range(1, 32)]
illegalCharacters += [chr(0x7F)]
reservedFileNames = "CON PRN AUX CLOCK$ NUL A:-Z: COM1".lower().split(" ")
reservedFileNames += "LPT1 LPT2 LPT3 COM2 COM3 COM4".lower().split(" ")
maxFileNameLength = 255

def userNameToFileName(userName, existing=[], prefix="", suffix=""):
    """
    existing should be a case-insensitive list
    of all existing file names.

    >>> userNameToFileName(u"a") == u"a"
    True
    >>> userNameToFileName(u"A") == u"A_"
    True
    >>> userNameToFileName(u"AE") == u"A_E_"
    True
    >>> userNameToFileName(u"Ae") == u"A_e"
    True
    >>> userNameToFileName(u"ae") == u"ae"
    True
    >>> userNameToFileName(u"aE") == u"aE_"
    True
    >>> userNameToFileName(u"a.alt") == u"a.alt"
    True
    >>> userNameToFileName(u"A.alt") == u"A_.alt"
    True
    >>> userNameToFileName(u"A.Alt") == u"A_.A_lt"
    True
    >>> userNameToFileName(u"A.aLt") == u"A_.aL_t"
    True
    >>> userNameToFileName(u"A.alT") == u"A_.alT_"
    True
    >>> userNameToFileName(u"T_H") == u"T__H_"
    True
    >>> userNameToFileName(u"T_h") == u"T__h"
    True
    >>> userNameToFileName(u"t_h") == u"t_h"
    True
    >>> userNameToFileName(u"F_F_I") == u"F__F__I_"
    True
    >>> userNameToFileName(u"f_f_i") == u"f_f_i"
    True
    >>> userNameToFileName(u"Aacute_V.swash") == u"A_acute_V_.swash"
    True
    >>> userNameToFileName(u".notdef") == u"_notdef"
    True
    >>> userNameToFileName(u"con") == u"_con"
    True
    >>> userNameToFileName(u"CON") == u"C_O_N_"
    True
    >>> userNameToFileName(u"con.alt") == u"_con.alt"
    True
    >>> userNameToFileName(u"alt.con") == u"alt._con"
    True
    >>> userNameToFileName(u"a*") == u"a_"
    True
    >>> userNameToFileName(u"a", [u"a"]) == u"a000000000000001"
    True
    """
    # the incoming name must be a unicode string
    assert isinstance(userName, unicode), "The value for userName must be a unicode string."
    # establish the prefix and suffix lengths
    prefixLength = len(prefix)
    suffixLength = len(suffix)
    # replace an initial period with an _
    # if no prefix is to be added
    if not prefix and userName[0] == ".":
        userName = "_" + userName[1:]
    # filter the user name
    filteredUserName = []
    for character in userName:
        # replace illegal characters with _
        if character in illegalCharacters:
            character = "_"
        # add _ to all non-lower characters
        elif character != character.lower():
            character += "_"
        filteredUserName.append(character)
    userName = "".join(filteredUserName)
    # clip to 255
    sliceLength = maxFileNameLength - prefixLength - suffixLength
    userName = userName[:sliceLength]
    # test for illegal files names
    parts = []
    for part in userName.split("."):
        if part.lower() in reservedFileNames:
            part = "_" + part
        parts.append(part)
    userName = ".".join(parts)
    # test for clash
    fullName = prefix + userName + suffix
    if fullName.lower() in existing:
        fullName = handleClash1(userName, existing, prefix, suffix)
    # finished
    return fullName

def handleClash1(userName, existing=[], prefix="", suffix=""):
    """
    existing should be a case-insensitive list
    of all existing file names.

    >>> prefix = ("0" * 5) + "."
    >>> suffix = "." + ("0" * 10)
    >>> existing = ["a" * 5]

    >>> e = list(existing)
    >>> handleClash1(userName="A" * 5, existing=e,
    ...     prefix=prefix, suffix=suffix)
    '00000.AAAAA000000000000001.0000000000'

    >>> e = list(existing)
    >>> e.append(prefix + "aaaaa" + "1".zfill(15) + suffix)
    >>> handleClash1(userName="A" * 5, existing=e,
    ...     prefix=prefix, suffix=suffix)
    '00000.AAAAA000000000000002.0000000000'

    >>> e = list(existing)
    >>> e.append(prefix + "AAAAA" + "2".zfill(15) + suffix)
    >>> handleClash1(userName="A" * 5, existing=e,
    ...     prefix=prefix, suffix=suffix)
    '00000.AAAAA000000000000001.0000000000'

    Max file length
    ---------------
    >>> handleClash1(userName="ABCDEFGHIJKLMNOPQRSTUVWX_" * 10,
    ...     prefix=prefix, suffix=suffix) # doctest: +ELLIPSIS
    '00000.ABCDEFGHIJKLM...NOPQRSTUVW000000000000001.0000000000'
    """
    # if the prefix length + user name length + suffix length + 15 is at
    # or past the maximum length, slice 15 characters off of the user name
    prefixLength = len(prefix)
    suffixLength = len(suffix)
    if prefixLength + len(userName) + suffixLength + 15 > maxFileNameLength:
        l = (prefixLength + len(userName) + suffixLength + 15)
        sliceLength = maxFileNameLength - l
        userName = userName[:sliceLength]
    finalName = None
    # try to add numbers to create a unique name
    counter = 1
    while finalName is None:
        name = userName + str(counter).zfill(15)
        fullName = prefix + name + suffix
        if fullName.lower() not in existing:
            finalName = fullName
            break
        else:
            counter += 1
        if counter >= 999999999999999:
            break
    # if there is a clash, go to the next fallback
    if finalName is None:
        finalName = handleClash2(existing, prefix, suffix)
    # finished
    return finalName

def handleClash2(existing=[], prefix="", suffix=""):
    """
    existing should be a case-insensitive list
    of all existing file names.

    >>> prefix = ("0" * 5) + "."
    >>> suffix = "." + ("0" * 10)
    >>> existing = [prefix + str(i) + suffix for i in range(100)]

    >>> e = list(existing)
    >>> handleClash2(existing=e, prefix=prefix, suffix=suffix)
    '00000.100.0000000000'

    >>> e = list(existing)
    >>> e.remove(prefix + "1" + suffix)
    >>> handleClash2(existing=e, prefix=prefix, suffix=suffix)
    '00000.1.0000000000'

    >>> e = list(existing)
    >>> e.remove(prefix + "2" + suffix)
    >>> handleClash2(existing=e, prefix=prefix, suffix=suffix)
    '00000.2.0000000000'
    """
    # calculate the longest possible string
    maxLength = maxFileNameLength - len(prefix) - len(suffix)
    maxValue = int("9" * maxLength)
    # try to find a number
    finalName = None
    counter = 1
    while finalName is None:
        fullName = prefix + str(counter) + suffix
        if fullName.lower() not in existing:
            finalName = fullName
            break
        else:
            counter += 1
        if counter >= maxValue:
            break
    # raise an error if nothing has been found
    if finalName is None:
        raise NameTranslationError("No unique name could be found.")
    # finished
    return finalName

# -------
# Testing
# -------

def _runProfile(outPath):
    normalizeUFO(outPath)

def runTests():
    # doctests
    import doctest
    doctest.testmod()

    # test file searching
    paths = []
    d = os.path.dirname(__file__)
    pattern = os.path.join(d, "test", "*.ufo")
    for inPath in glob.glob(pattern):
        if inPath.endswith("-n.ufo"):
            continue
        outPath = os.path.splitext(inPath)[0] + "-n.ufo"
        if os.path.exists(outPath):
            shutil.rmtree(outPath)
        paths.append((inPath, outPath))

    if paths:
        # profile test
        import cProfile

        inPath, outPath = paths[0]
        shutil.copytree(inPath, outPath)

        cProfile.run("_runProfile('%s')" % outPath, sort="tottime")
        shutil.rmtree(outPath)

        # general test
        import time

        for inPath, outPath in paths:
            shutil.copytree(inPath, outPath)
            s = time.time()
            normalizeUFO(outPath)
            t = time.time() - s
            print(os.path.basename(inPath) + ":", t, "seconds")


if __name__ == "__main__":
    main()
