# Station Manager (`mgm8`)

Controla o rotor da estação terrestre do SpaceLab e conduz sozinho uma passagem
de satélite, do AOS ao LOS.

Recebe **uma ordem por passagem** (`track_satellite`, com os elementos orbitais
e a hora do LOS) e a partir daí propaga, calcula o apontamento e comanda o rotor
sem depender de mais ninguém. É por isso que uma queda do TC Scheduler no meio
de uma passagem não a interrompe.

Faz parte da estação terrestre orquestrada em
[nanosat-gs/grs-station](https://github.com/nanosat-gs/grs-station).

## Interface

Uma porta só: **ZMQ REP em 5580**, JSON. O protocolo está documentado no
docstring de [`src/mgm8/rotor_zmq/server.py`](src/mgm8/rotor_zmq/server.py),
que é a fonte canônica para os clientes.

```json
{"cmd": "set_target", "azimuth_degrees": 180.0, "elevation_degrees": 45.0}
{"cmd": "track_satellite", "orbital_data": {...}, "until": "2026-08-27T14:30:00+00:00"}
{"cmd": "get_tracking"}
```

Sem HTTP e sem banco, de propósito: o serviço precisa poder rodar na máquina do
rádio, longe do resto da estação.

## Instalar e rodar

```bash
pip install -e ".[dev]"
pytest

python -m mgm8.rotor_zmq.main --bind=tcp://0.0.0.0:5580 --rotor=mock
```

`--rotor zmq` fala com o rotor físico ou com `tools/rotor_simulator.py`. Isso
não funciona dentro do Docker — ver a seção de armadilhas no `CLAUDE.md`.

## Estrutura

```
src/mgm8/
├── application/     # Casos de uso: controle de rotor e rastreamento de passagem
├── domain/          # Entidades, value objects e portas
├── infrastructure/  # Adapters de saída: rotor mock/ZMQ, apontamento SGP4
├── vendor/          # Rotor Manager copiado do grs-rotor-manager (ver UPSTREAM.md)
└── rotor_zmq/       # Adapter de entrada ZMQ e composition root
```
