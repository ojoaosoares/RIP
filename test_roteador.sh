#!/bin/bash

# Test script for the RC_RIP router implementation

# Port mapping from roteadores_locais.txt
# vulcan: 11111
# kronos: 22222
# risa: 33333
# terra: 44444

nomes=(vulcan kronos risa terra)
portos=(11111 22222 33333 44444)

echo "Starting routers in the background..."
pids=()
for i in 0 1 2 3
do
    log="log.${nomes[i]}"
    echo "Starting ${nomes[i]} on port ${portos[i]} (log in $log)..."
    python3 roteador.py ${portos[i]} > "$log" 2>&1 &
    pids+=($!)
done

# Wait for routers to bind to sockets
sleep 2

echo "Running control program with test_comandos.txt..."
python3 controle.py roteadores_locais.txt < test_comandos.txt > log.controle 2>&1

echo "Waiting for final messages to settle..."
sleep 2

# Cleanup background processes
echo "Cleaning up router processes..."
for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null
done
# Backup cleanup just in case
pkill -f "python3 roteador.py" 2>/dev/null

echo "=== CONTROL LOG ==="
cat log.controle

echo ""
echo "=== VULCAN LOG ==="
cat log.vulcan

echo ""
echo "=== RISA LOG ==="
cat log.risa

echo ""
echo "=== KRONOS LOG ==="
cat log.kronos

echo ""
echo "=== TERRA LOG ==="
cat log.terra

echo "Done!"
