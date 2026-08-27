# Código copiado de terceiros

## `rotor_manager.py`

- **Origem:** https://github.com/spacelab-ufsc/grs-rotor-manager
- **Commit:** `7c72dc541b563e5bc0d0f4bdc09a776f781fb433` (`main`)
- **Copiado em:** 2026-08-27
- **Licença:** o repositório de origem não declara licença. É código do próprio
  SpaceLab, usado aqui dentro do laboratório; uma redistribuição externa
  precisa resolver isso antes.
- **Alterações:** nenhuma. Cópia byte a byte do upstream.

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

`RotorManager.__init__` tem o socket SUB de status fixo em
`tcp://localhost:5560`, o que só funciona com o simulador no mesmo host de
rede — nunca entre containers. É por isso que o `docker-compose.yml` força
`--rotor mock`, e por isso o rotor real continua rodando fora do Docker.

Com o arquivo sob controle, parametrizar esse endereço vira uma mudança
possível. Não foi feita junto da cópia de propósito: mover código e mudar
comportamento no mesmo passo torna impossível saber qual dos dois quebrou.
