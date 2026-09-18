# Código copiado de terceiros

## `rotor_manager.py`

- **Origem:** https://github.com/spacelab-ufsc/grs-rotor-manager
- **Commit:** `7c72dc541b563e5bc0d0f4bdc09a776f781fb433` (`main`)
- **Copiado em:** 2026-08-27
- **Licença:** o repositório de origem não declara licença. É código do próprio
  SpaceLab, usado aqui dentro do laboratório; uma redistribuição externa
  precisa resolver isso antes.
- **Alterações:** `RotorManager.__init__` ganhou um segundo parâmetro opcional
  `status_address` (default `tcp://localhost:5560`, o mesmo valor fixo de
  antes — comportamento idêntico para quem não passar o argumento). Ver "O que
  a cópia destrava" abaixo.

`tools/rotor_simulator.py` veio do mesmo commit, também sem alteração. Ele não
é instalado com o pacote — serve para exercitar o caminho `--rotor zmq` sem
hardware.

### Por que copiado, e não submódulo

Era um submódulo em `vendor/grs-rotor-manager/`, alcançado por um
`sys.path.insert` que subia três diretórios a partir de
`infrastructure/rot2prog_zmq.py`. Isso amarrava o import ao formato da árvore:
o arquivo deixava de ser encontrável assim que o pacote mudasse de lugar.

São 88 linhas de um upstream somente-leitura, onde não há para onde mandar
commit. O submódulo cobrava toda a sua cerimônia (`--recursive`, ponteiro,
`.gitmodules`) sem entregar o que ela compra.

### O que a cópia destrava

`RotorManager.__init__` tinha o socket SUB de status fixo em
`tcp://localhost:5560`, o que só funcionava com o simulador no mesmo host de
rede — nunca entre containers. Não foi parametrizado junto da cópia de
propósito: mover código e mudar comportamento no mesmo passo torna impossível
saber qual dos dois quebrou.

Esse endereço agora é parametrizável (`status_address`, ver "Alterações"
acima) e exposto pelo Station Manager via `--rotor-status-address` (ver
`mgm8.rotor_zmq.main`). O `docker-compose.yml` continua forçando `--rotor
mock` porque isso resolve só metade do problema — o hardware/simulador em si
ainda roda fora do Docker; o rotor real entre containers segue como trabalho
futuro.
