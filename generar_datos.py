import json
import random

def generar_mock_data():
    print("🏭 Generando 150 Departamentos con datos detallados...")
    
    unidades = []
    apellidos = ["Silva", "Gomez", "Perez", "Gonzalez", "Muñoz", "Rojas", "Diaz", "Vasquez", "Castro"]
    nombres = ["Ana", "Carlos", "Roberto", "Maria", "Jose", "Luis", "Elena", "Sofia", "Miguel"]
    corredoras = ["Propiedades Pro", "Gestión Inmobiliaria", "Corredora Santiago", "Tu Casa OK"]

    # Generar 15 Pisos
    for piso in range(1, 16):
        # 10 Departamentos por piso
        for d in range(1, 11):
            numero_depto = f"{piso}{d:02d}" # Ej: 101, 102... 1510
            
            # Datos Aleatorios
            es_arrendado = random.choice([True, False])
            tiene_corredora = es_arrendado and random.choice([True, False])
            
            metraje = random.choice([45.5, 60.0, 85.5, 120.0])
            prorrateo = round(metraje * 0.015, 3) # Cálculo simple de prorrateo
            
            # Generar Dueño
            nom_owner = f"{random.choice(nombres)} {random.choice(apellidos)}"
            rut_owner = f"{random.randint(10,25)}.{random.randint(100,999)}.{random.randint(100,999)}-{random.randint(0,9)}"
            
            owner_data = {
                "rut": rut_owner,
                "nombre": nom_owner,
                "email": f"{nom_owner.split()[0].lower()}@mail.com",
                "fono": f"+569{random.randint(10000000, 99999999)}"
            }

            # Generar Residente (Si es arrendado es otro, si no, es el dueño)
            if es_arrendado:
                nom_tenant = f"{random.choice(nombres)} {random.choice(apellidos)}"
                rut_tenant = f"{random.randint(15,30)}.{random.randint(100,999)}.{random.randint(100,999)}-{random.randint(0,9)}"
                tenant_data = {
                    "rut": rut_tenant,
                    "nombre": nom_tenant,
                    "email": f"{nom_tenant.split()[0].lower()}@live.cl",
                    "fono": f"+569{random.randint(10000000, 99999999)}"
                }
            else:
                tenant_data = owner_data # El dueño vive ahí

            # Generar Corredora
            if tiene_corredora:
                broker_data = {
                    "rut": "77.000.000-K",
                    "nombre": random.choice(corredoras),
                    "email": "contacto@corredora.cl",
                    "fono": "+56222222222"
                }
            else:
                broker_data = {"rut": "", "nombre": "No aplica", "email": "", "fono": ""}

            unidad = {
                "numero": numero_depto,
                "piso": piso,
                "metraje": metraje,
                "prorrateo": prorrateo,
                "estacionamiento": f"E-{random.randint(1,200)}",
                "bodega": f"B-{random.randint(1,150)}",
                "owner": owner_data,
                "tenant": tenant_data,
                "broker": broker_data
            }
            
            unidades.append(unidad)

    # Guardar en archivo JSON
    with open('carga_masiva.json', 'w', encoding='utf-8') as f:
        json.dump(unidades, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Archivo 'carga_masiva.json' creado con {len(unidades)} unidades.")

if __name__ == "__main__":
    generar_mock_data()