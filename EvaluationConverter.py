import config
import ShapelyHelper, EvaluationFileOps
from shapely.ops import unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry import shape, mapping, shape, asShape
from shapely.geometry import MultiPolygon, MultiPoint, MultiLineString
from shapely.validation import explain_validity
from shapely import speedups
if speedups.available:
        speedups.enable()   
import sqlite3 as sqlite
import fiona 
import time
import shutil
from fiona.crs import to_string
import  json, geojson
from sqlitedict import SqliteDict
import os, sys
from os import listdir
from os.path import isfile, join
import os.path as osp
import logging
import Colorer
import zipfile
from collections import defaultdict
# LOG_FILENAME = "runlog.log"
loggers = {}
def configure_logging(name):
    global loggers
    if loggers.get(name):
        return loggers.get(name)
    else:    
        logger = logging.getLogger("evals logger")
        logger.setLevel(logging.ERROR)
        # Format for our loglines
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        # Setup console logging
        ch = logging.StreamHandler()
        ch.setLevel(logging.ERROR)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        # Setup file logging as well
        # fh = logging.FileHandler(LOG_FILENAME)
        # fh.setLevel(logging.DEBUG)
        # fh.setFormatter(formatter)
        # logger.addHandler(fh)
        logger.propagate = False
        return logger

curPath = os.path.dirname(os.path.realpath(__file__))

class OpStatus():
    
    def __init__(self):
        self.stages = {}
        for i in range(1,8):
            x = {'status':3, 'errors':[],'warnings':[], 'info':[], 'debug':[], 'success':[], 'statustext':""}
            self.stages[i] = x
        self.current_milli_time = lambda: int(round(time.time() * 1000))
    
    def add_warning(self, stage, msg):
        self.stages[stage]['warnings'].append({'msg':msg,'time':self.current_milli_time()})

    def add_success(self, stage, msg):
        self.stages[stage]['success'].append({'msg':msg,'time':self.current_milli_time()})
        
    def add_error(self, stage, msg):
        self.stages[stage]['errors'].append({'msg':msg,'time':self.current_milli_time()})
        
    def add_info(self, stage, msg):
        self.stages[stage]['info'].append({'msg':msg,'time':self.current_milli_time()})

    def add_debug(self, stage, msg):
        self.stages[stage]['debug'].append({'msg':msg,'time':self.current_milli_time()})

    def set_statustext(self, stage, msg):
        self.stages[stage]['statustext'] = msg

    def set_status(self, stage, status, statustext=None):
        self.stages[stage]['status']= status
        if statustext:
            self.stages[stage]['statustext']= statustext

    def get_all_status(self):
        allstatus = {}
        for stage, results in self.stages.items():
            allstatus[stage] = results['status']
        return allstatus

    def get_allstatuses(self):
        return json.dumps(self.stages)


class ConvertEvaluation():
    '''
    There are seven stages to the process 
    1. Check if Zip file unzips properly
    2. Check if there is a Geopackage in the zip
    3. Check if the features and schema is correct
    4. Reproject the file
    5. Simplify the file
    6. Convert to geojson
    7. Check performance: 
        - Number of features
        - Errors in intersection
        - time required

    Status: 
    0 - Error / Failed
    1 - Success /OK
    2 - Warnings
    3 - Not started
    4 - Information

    '''
    def __init__(self):
        self.SOURCE_FILE_SHARE = os.path.join(curPath, config.inputs['directory'])
        self.WORKING_SHARE = os.path.join(curPath, config.working['directory'])
        # final GEOJSON
        self.OUTPUT_SHARE = os.path.join(curPath, config.geojsonoutput['directory'])
        self.logger = configure_logging('evals logger')
        self.opstatus = OpStatus()
        
    def convert(self):
        def isSQLite(filename):
            """True if filename is a SQLite database
            File is database if: (1) file exists, (2) length is non-zero,
                                (3) can connect, (4) has sqlite_master table
            """
            # validate file exists
            if not osp.isfile(filename):
                return False
            # is not an empty file
            if not os.stat(filename).st_size:
                return False
            # can open a connection
            try:
                conn = sqlite.connect(filename)
            except Exception as ae:
                return False
            # has sqlite_master
            try:
                result = conn.execute('pragma table_info(sqlite_master)').fetchall()
                if len(result) == 0:
                    conn.close()
                    return False
            except:
                conn.close()
                return False

            # looks like a good database
            else:
                conn.close()
            
            return True 

        self.logger.info("Geodesignhub Evaluations Converter")    
        myShpFileHelper = EvaluationFileOps.GeopackageHelper(self.opstatus)
        self.logger.info("Reading source files.. ")
        curPath = os.path.dirname(os.path.realpath(__file__))
        allBounds = []
        if not os.path.exists(self.WORKING_SHARE):
            os.mkdir(self.WORKING_SHARE)
        if not os.path.exists(self.OUTPUT_SHARE):
            os.mkdir(self.OUTPUT_SHARE)
        try:
            assert os.path.exists(self.SOURCE_FILE_SHARE)
        except AssertionError as e:
            self.logger.error("Source file directory does not exist, please check config.py for correct filename and directory")
        # Read the zip files


        # # Geopackage has been read, read the data.

        gpkgfiles = [f for f in listdir(self.SOURCE_FILE_SHARE) if (isfile(os.path.join(self.SOURCE_FILE_SHARE, f)) and (os.path.splitext(f)[1] == '.gpkg'))]
    
        ferror = False
        for g in gpkgfiles:  
            try:
                assert isSQLite(os.path.join(self.SOURCE_FILE_SHARE, g))                
            except AssertionError as e:
                ferror = True
                self.logger.error("Error in reading gpkg file %s" %e)


                
                
        if ferror: 
            self.opstatus.set_status(stage=1, status=0, statustext ="Problem with opening and reading gpkg file contents.")
            self.opstatus.add_error(stage=1, msg = "Problems with your gpkg file, please make sure that it is not curropt.")
        else:
            self.opstatus.set_status(stage=1, status=1, statustext ="gpkg file read without problems")
            self.opstatus.add_success(stage=1, msg = "File contents read successfully")
            
        myFileOps = EvaluationFileOps.FileOperations(self.SOURCE_FILE_SHARE, self.OUTPUT_SHARE, self.WORKING_SHARE,self.opstatus)
        allGJ = {}
        geometrysuccess= 0
        reprojectstatus = 0
        if (ferror == False) and gpkgfiles and len(gpkgfiles)==1:
            self.opstatus.set_status(stage=2, status=1, statustext ="GeoPackage was found in the archive")
            self.opstatus.add_success(stage=2, msg = "Geopackage extracted successfully and contents read")
            for f in gpkgfiles:
                filepath = os.path.join(self.SOURCE_FILE_SHARE, f)

                # validate features and schema
                with fiona.open(filepath, driver='GPKG') as curfile:

                    schema = curfile.schema
                    schemavalidates = myShpFileHelper.validateSchema(schema)    
                    featuresvalidate = myShpFileHelper.validateFeatures(curfile)

                try: 
                    assert schemavalidates
                    self.logger.info("Every feature is a polygon")
                    self.opstatus.add_info(stage=3, msg = "Every feature is a polygon")
                except AssertionError as e:
                    self.logger.error("Your file has features that are not 'Polygons', please ensure that all 3D Polygons etc. are removed.")
                    self.opstatus.add_error(stage=3, msg = "Input Geopackage does not have the correct geometry. Your file has features that are not 'Polygons', please ensure that all 3D Polygons etc. are removed.")
                    
                try: 
                    assert featuresvalidate
                    self.logger.info("Every feature as the correct areatype")
                    self.opstatus.add_info(stage=3, msg = "Every feature has the correct areatype value one of: red, yellow, green, green2, green3")
                except AssertionError as e: 
                    self.logger.error("Features in a Geopackage must have allowed areatype attributes")
                    self.opstatus.add_error(stage=3, msg = "Features in a Geopackage must have allowed areatype attributes")
                    
                
                
                if schemavalidates and featuresvalidate:
                        self.opstatus.set_status(stage=3, status=1, statustext ="Geopackage has the areatype column and correct values in the attribtute table.")
                        self.opstatus.add_success(stage=3, msg = "Geopackage has the areatype column and correct values in the attribtute table")
                else:
                    self.opstatus.set_status(stage=3, status=0, statustext ="A areatype attribute is either not present or have the correct value or the features are not 'Polygon' geometry. For further information please refer: <a href='https://community.geodesignhub.com/t/geojson-shapefile-feature-attributes/55' target='_blank'>GeoJSON / Geopackage feature attributes</a>")
                    if not featuresvalidate:
                        self.opstatus.add_error(stage=3, msg = "Your Geopackage attribute table must have a areatype column with the correct attribute and all features should be 'Polygon' geometry.")
                    if not schemavalidates:                        
                        self.opstatus.add_error(stage=3, msg = "Your Geopackage does not have the correct values for the areatype column, it has to be one of  red, yellow, green, green2, green3")

                # Reproject the file. 
                if schemavalidates and featuresvalidate:
                    spfile = myFileOps.multipart_to_singlepart(filepath)
                    reprojectedfile, hasReprojErrors = myFileOps.reprojectFile(spfile)

                    if hasReprojErrors:
                        self.opstatus.set_status(stage=4, status=4, statustext ="There were errors in reprojecting some features, they are removed from output.")
                    else: 
                        self.opstatus.set_status(stage=4, status=1, statustext ="Geopackage reprojected successfully")

                    self.opstatus.add_success(stage=4, msg = "Reprojected file successfully written successfully")

                    simplifiedfile, bounds = myFileOps.simplifyReprojectedFile(reprojectedfile)
                    
                    allBounds.append(bounds)
                    try:

                        gjFile = myShpFileHelper.convert_gpkg_to_geojson(simplifiedfile, self.WORKING_SHARE) 
                    except Exception as e: 
                        self.logger.error("Error in converting Geopackage to Geojson %s" %e)
                        self.opstatus.set_status(stage=6, status=0, statustext ="Error in converting Geopackage to GeoJSON")
                        self.opstatus.add_error(stage=6, msg = "Error in converting Geopackage to GeoJSON %s" %e)

                    with open(gjFile,'r') as gj:
                        allGJ[f] = json.loads(gj.read())

                else: 
                    self.opstatus.set_status(stage=4, status=0, statustext ="There are errors in file attribute table, reprojection not started")
                    self.opstatus.add_error(stage=4, msg = "Check the attribute table for areatype column and correct areatype value.")
                    self.opstatus.set_status(stage=5, status=0, statustext ="File attribute table does not validate, therefore will not simplify")
                    self.opstatus.add_error(stage=5, msg = "Check the attribute table for areatype column and correct areatype value")
                    self.opstatus.set_status(stage=6, status=0, statustext ="Geopackage not converted to GeoJSON. ")
                    self.opstatus.add_error(stage=6, msg = "File will not be converted to GeoJSON, see earlier errors")
                    self.opstatus.set_status(stage=7, status=0, statustext ="Performance testing not started, please upload the correct file")
                    self.opstatus.add_error(stage=7, msg = "File performance will not be checked, please review earlier errors")

            # TODO: make this multifile
            try:
                assert 0 in set(self.opstatus.get_all_status().values())
                self.opstatus.set_status(stage=7, status=0, statustext= "There were errors in pervious stages, performance testing will not be conducted until they are resolved. ")
            except AssertionError as ea:                  
                self.logger.info("Starting perfomrance analysis")
                myGeomOps = ShapelyHelper.GeomOperations()
                allBounds = myGeomOps.calculateBounds(allBounds)
                allBounds = allBounds.split(',')
                allBounds = [float(i) for i in allBounds]

                evalulationColors = ['red2','red', 'yellow', 'green', 'green2','green3']
                evalPaths = [f for f in listdir(self.WORKING_SHARE) if (isfile(join(self.WORKING_SHARE, f)) and (os.path.splitext(f)[1] == '.geojson'))]
                # generate random features

                featData = {"type":"FeatureCollection", "features":[]}
                myGJHelper = ShapelyHelper.GeoJSONHelper()
                
                self.logger.info("Generating random features within the bounds")
                self.opstatus.add_info(stage=7, msg = "Generating random features within the evaluation feature bounds")
                for i in range(5):
                    x = myGJHelper.genRandom(featureType="Polygon", numberVertices=4, boundingBox= allBounds)
                    f = {"type": "Feature", "properties": {},"geometry": json.loads(geojson.dumps(x))}
                    featData['features'].append(f)

                # polygonize the features
                combinedPlanPolygons = []
                for feature in featData['features']:
                    combinedPlanPolygons.append(asShape(feature['geometry']))
                allPlanPolygons = MultiPolygon([x for x in combinedPlanPolygons if x.geom_type == 'Polygon' and x.is_valid])
                allPlanPolygons = unary_union(allPlanPolygons)
                # read the evaluations
                timetaken = []
                for fname in evalPaths:
                    self.logger.debug("Currently processsing: %s" % fname)
                    self.opstatus.add_info(stage=7, msg = "Currently processsing: %s" % fname)

                    evalFPath = os.path.join(self.WORKING_SHARE, fname)
                    cacheKey = os.path.basename(evalFPath)
                    
                    filepath = os.path.join(self.WORKING_SHARE, 'some.db')
                    s = SqliteDict(filepath, autocommit=False)
                    # read the evaluation file
                    with open(evalFPath, 'r') as gjFile:
                            data = gjFile.read()
                            evalData = json.loads(data)

                    colorDict =  {'red':[],'red2':[], 'yellow':[],'green':[],'green2':[], 'green3':[],'constraints':[]}
                    errorDict =  {'red':0,'red2':0, 'yellow':0,'green':0,'green2':0,'green3':0, 'constraints':0}

                    for curFeature in evalData['features']:
                        areatype = curFeature['properties']['areatype']
                        errorCounter = errorDict[areatype]
                        shp, errorCounter = myGeomOps.genFeature(curFeature['geometry'], errorCounter)
                        errorDict[areatype] = errorCounter
                        if shp:
                            colorDict[areatype].append(shp) 
                        
                    self.logger.info("Geometry errors in %(A)s Red2, %(B)s Red, %(C)s Yellow, %(D)s Green, %(E)s Green2, %(F)s Green3 and %(G)s Constraints features." % {'A' : errorDict['red2'], 'B' : errorDict['red'], 'C':errorDict['yellow'], 'D':errorDict['green'], 'E':errorDict['green2'],'F':errorDict['green3'], 'G': errorDict['constraints']})

                    self.opstatus.add_info(stage=7, msg = "Geometry errors in %(A)s Red2, %(B)s Red, %(C)s Yellow, %(D)s Green, %(E)s Green2, %(F)s Green3 and %(G)s Constraints features." % {'A' : errorDict['red2'], 'B' : errorDict['red'], 'C':errorDict['yellow'], 'D':errorDict['green'], 'E':errorDict['green2'],'F':errorDict['green3'], 'G': errorDict['constraints']})
                
                    # self.logger.debug(len(colorDict['red2']), len(colorDict['red']), len(colorDict['yellow']), len(colorDict['green']),len(colorDict['green2']),len(colorDict['green3']),len(colorDict['constraints']))
                    x = "Processed " + str(len(colorDict['red2'])) + " Red2, "+  str(len(colorDict['red']))+ " Red, "+ str(len(colorDict['yellow']))+ " Yellow, "+ str(len(colorDict['green']))+ " Yellow, "+str(len(colorDict['green2']))+ " Yellow, "+str(len(colorDict['green3']))+ " Green3 features."
                    
                    self.opstatus.add_info(stage=7, msg = x)

                    import time
                    start_time = time.time()

                    # create a union and write to SqliteDict this is to test caching performance.   
                    for k in colorDict.keys():
                        u = myGeomOps.genUnaryUnion(colorList=colorDict[k])
                        curCacheKey = cacheKey + '-' + k
                        if curCacheKey not in s.keys() and u:
                            s[curCacheKey] = u
                    s.commit()
                    self.logger.debug("--- %.4f seconds ---" % float(time.time() - start_time))
                    timetaken.append(float(time.time() - start_time))
                    self.opstatus.set_statustext(stage=7, msg = "Processing took %.4f seconds " % float(time.time() - start_time))
                    # -- write to union json file
                    for k in colorDict.keys():
                        curCacheKey = cacheKey+ '-' + k
                        try:
                            u = s[curCacheKey]
                        except KeyError as e: 
                            u = []
                        if u:
                            featureCollectionList = []
                            allJSON = ShapelyHelper.export_to_JSON(u)
                            featureCollectionList.append(myGeomOps.constructSingleFeatureDef(allJSON,k))
                            outputJSON = {}
                            outputJSON["type"] = "FeatureCollection"
                            outputJSON["features"]= featureCollectionList
                            fname = k + '.json'
                            uf = os.path.join(self.OUTPUT_SHARE, fname)
                            with open(uf, 'w') as outFile:
                                json.dump(outputJSON , outFile)
                    # -- write to intersection json file
                    for k in colorDict.keys():
                        curCacheKey = cacheKey+ '-' + k
                        self.logger.debug("%s intersection starts" % k)
                        # self.opstatus.add_debug(stage=7, msg = "%s intersection starts" % k)
                        fname = k + '-intersect.json'
                        o = os.path.join(self.OUTPUT_SHARE, fname)
                        try:
                            evalFeats = s[curCacheKey]
                        except KeyError as e: 
                            evalFeats = []
                        if evalFeats:
                            with open(o, 'w') as outFile:
                                op, geometrysuccess = myGeomOps.checkIntersection(allPlanPolygons,evalFeats, k)
                                json.dump( op, outFile)
                    
                        else: 
                            self.logger.info("No %s features in input evaluation." % k)
                            self.opstatus.add_info(stage=7, msg = "No %s features in evaluation file." % k)
                
                if max(timetaken) > 4.0:
                    self.opstatus.set_status(stage=7, status=0, statustext= "Your file is either too large or is taking too much time to process, it is recommended that you reduce the features or simplify them.")
                elif geometrysuccess ==0: 
                    self.opstatus.set_status(stage=7, status=0, statustext= "Your file has topology and geometry errors. Please fix them and try again. ")
                else:
                    self.opstatus.set_status(stage=7, status=1)
        else:
            self.logger.warning("Could not find the gkpg file.")
            self.opstatus.set_status(stage=2, status=0, statustext ="Could not find .gpkg file.")
            self.opstatus.add_error(stage=2, msg = "Please ensure that you are upload Geopackage file with a .gpkg extension.")

        return allGJ , self.opstatus.get_allstatuses()

    def cleanDirectories(self):
        dirs = [self.WORKING_SHARE, self.SOURCE_FILE_SHARE, self.OUTPUT_SHARE]
        for folder in dirs:
            for the_file in os.listdir(folder):
                file_path = os.path.join(folder, the_file)
                try:
                    if (os.path.isfile(file_path) and (the_file != 'README')):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path): shutil.rmtree(file_path)
                except Exception as e:
                    
                    self.logger.error("Error Clearing out share. %s " % e)