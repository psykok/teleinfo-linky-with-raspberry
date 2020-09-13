#!/usr/bin/env python
# -*- coding: utf-8 -*-
# __author__ = "Sébastien Reuiller"
# __licence__ = "Apache License 2.0"

# Python 3, prerequis : pip install pySerial influxdb
#
# Exemple de trame:
# {
#  'BASE': '123456789'       # Index heure de base en Wh
#  'OPTARIF': 'HC..',        # Option tarifaire HC/BASE
#  'IMAX': '007',            # Intensité max
#  'HCHC': '040177099',      # Index heure creuse en Wh
#  'IINST': '005',           # Intensité instantanée en A
#  'PAPP': '01289',          # Puissance Apparente, en VA
#  'MOTDETAT': '000000',     # Mot d'état du compteur
#  'HHPHC': 'A',             # Horaire Heures Pleines Heures Creuses
#  'ISOUSC': '45',           # Intensité souscrite en A
#  'ADCO': '000000000000',   # Adresse du compteur
#  'HCHP': '035972694',      # index heure pleine en Wh
#  'PTEC': 'HP..'            # Période tarifaire en cours
# }


import serial
import logging
import time
import requests
from datetime import datetime
from influxdb import InfluxDBClient
mode = "DEBUG"  # DEBUG, INFO

# clés téléinfo
char_measure_keys = ['DATE', 'NGTF', 'LTARF', 'MSG1', 'NJOURF', 'NJOURF+1', 'PJOURF', 'PJOURF+1','EASD02', 'STGE', 'RELAIS' ]

# création du logguer
logging.basicConfig(filename='/var/log/teleinfo/releve.log', level=logging.INFO, format='%(asctime)s %(message)s')
logging.info("Teleinfo starting..")

# connexion a la base de données InfluxDB
client = InfluxDBClient('localhost', 8086)
db = "teleinfo2"
connected = False
while not connected:
    try:
        logging.info("Database %s exists?" % db)
        if not {'name': db} in client.get_list_database():
            logging.info("Database %s creation.." % db)
            client.create_database(db)
            logging.info("Database %s created!" % db)
        client.switch_database(db)
        logging.info("Connected to %s!" % db)
    except requests.exceptions.ConnectionError:
        logging.info('InfluxDB is not reachable. Waiting 5 seconds to retry.')
        time.sleep(5)
    else:
        connected = True


def add_measures(measures, time_measure):
    points = []
    for measure, value in measures.items():
        point = {
                    "measurement": measure,
                    "tags": {
                        # identification de la sonde et du compteur
                        "host": "raspberry",
                        "region": "linky"
                    },
                    "time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "fields": {
                        "value": value
                    }
                }
        points.append(point)

    client.write_points(points)


def verif_checksum(line_str, checksum):
    data_unicode = 0
    data = line_str[0:-2] #chaine sans checksum de fin
    for caractere in data:
            data_unicode += ord(caractere)
    sum_unicode = (data_unicode & 63) + 32
    sum = chr(sum_unicode)
    if (checksum == sum):
        return True
    else:
        return False


def keys_from_file(file):
    labels = []
    #"available_linky_standard_keys.txt"
    with open(file) as f:
        for line in f:
           information = line.split("\t")
           labels.append(information[1])
    return labels


def dico_from_file(file):
    information = {}
    with open(file) as f:
        for line in f:
            line = line.replace("\n","")
            decoupage = line.split("\t")
            k = int(decoupage[0])
            v = decoupage[1]
            information[k] = v
    return information


def main():
    with serial.Serial(port='/dev/ttyAMA0', baudrate=9600, parity=serial.PARITY_ODD, bytesize=serial.SEVENBITS, timeout=1) as ser:
        # stopbits=serial.STOPBITS_ONE, 
        logging.info("Teleinfo is reading on /dev/ttyAMA0..")
        logging.info("Mode standard")
    
        labels_linky = keys_from_file("/opt/teleinfo-linky-with-raspberry/liste_champs_mode_standard.txt")
        liste_fabriquants = dico_from_file("/opt/teleinfo-linky-with-raspberry/liste_fabriquants_linky.txt")
        #liste_modeles = keys_from_file("/opt/teleinfo-linky-with-raspberry/modeles_linky.txt")
        
        trame = dict()

        # boucle pour partir sur un début de trame
        line = ser.readline()
        while b'\x02' not in line:  # recherche du caractère de début de trame
            line = ser.readline()

        # lecture de la première ligne de la première trame
        line = ser.readline()

        while True:
            #logging.debug(line)
            line_str = line.decode("utf-8")
            ar = line_str.split("\t") # separation sur tabulation

            try:
                key = ar[0]
                #checksum = ar[-1] #dernier caractere
                #verification = verif_checksum(line_str,checksum)
                #logging.debug("verification checksum :  s%" % str(verification)) 

                if (key in labels_linky):
                    if key in char_measure_keys:  # typer les valeurs connus sous forme de chaines en "string"
                        value = ar[-2]
                    else:
                        try:
                            value = int(ar[-2])   # typer les autres valeurs en "integer"
                        except:
                            logging.info("erreur de conversion en nombre entier")
                            value = 0

                    trame[key] = value   # creation du champ pour la trame en cours
                else:
                    trame['verification_error'] = "1"
                    logging.DEBUG("erreur etiquette inconnue")

                if b'\x03' in line:  # si caractère de fin de trame, on insère la trame dans influx
                    time_measure = time.time()
                    
                    # ajout nom fabriquant
                    numero_compteur = str(trame['ADSC'])
                    id_fabriquant = int(numero_compteur[2:4])
                    trame['OEM'] = liste_fabriquants[id_fabriquant]
                   
                    # ajout du CosPhi calculé
                    if (trame["IRMS1"] and trame["URMS1"] and trame["SINSTS"]):
                        trame["COSPHI"] = (trame["SINSTS"] / (trame["IRMS1"] * trame["URMS1"]))
                    logging.debug(trame["COSPHI"])
                    # ajout timestamp pour debugger
                    trame["timestamp"] = int(time_measure)

                    # insertion dans influxdb
                    add_measures(trame, time_measure)

                   #logging.debug(trame)

                    trame = dict()  # on repart sur une nouvelle trame
            except Exception as e:
                logging.debug("erreur traitement etiquette: %s" % key)
                #logging.error("Exception : %s" % e, exc_info=True)
                #logging.error("Ligne brut: %s \n" % line)
            line = ser.readline()


if __name__ == '__main__':
    if connected:
        main()


