# João Vitor Soares Santos
# TP2 - DCC/UFMG - Redes de Computadores
# Implementação de Roteador com Algoritmo de Vetor de Distâncias (RC_RIP)

import socket
import sys
import select
import struct
import threading

# Lock reentrante para sincronização de acesso à tabela e conexões
tabela_lock = threading.RLock()

# Estruturas de dados globais
nome_proprio = ""
vizinhos = {}  # nome_vizinho -> socket
tabela = {}    # destino -> {'nexthop': nexthop, 'dist': dist}

# Controle do temporizador periódico
INTERVALO = 1.0
timer_ativo = False
timer_lock = threading.Lock()

# Funções de extração de bytes vindos do programa de controle
def extrai_roteador(msg):
    r = struct.unpack("!32s", msg)
    return r[0].decode('utf-8', errors='replace')

def extrai_endereco(msg):
    r = struct.unpack("!32sH", msg)
    return r[0].decode('utf-8', errors='replace'), r[1]

def extrai_destino_texto(msg):
    l = struct.unpack("!32s64s", msg)
    destino = l[0].decode('utf-8', errors='replace')
    texto   = l[1].decode('utf-8', errors='replace')
    return destino, texto

# Recepção segura de n bytes de um stream TCP
def receber_bytes(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Conexão fechada")
        buf += chunk
    return buf

# Monta a mensagem do protocolo RC_RIP ('V') para enviar a um vizinho específico (com poison reverse)
def montar_mensagem_vetor(viz_name):
    entradas = []
    with tabela_lock:
        for dest, info in tabela.items():
            dist = info['dist']
            # Poison reverse: se o próximo salto é o próprio vizinho, anuncia infinito (16)
            if info['nexthop'] == viz_name and dest != viz_name:
                dist = 16
            entradas.append((dest, dist))
            
    N = len(entradas)
    msg = b'V'
    msg += struct.pack('!32s', nome_proprio.encode('utf-8'))
    msg += struct.pack('!H', N)
    for dest, dist in entradas:
        msg += struct.pack('!32sB', dest.encode('utf-8'), dist)
    return msg

# Desmonta a mensagem do protocolo RC_RIP ('V') recebida de um vizinho
def desmontar_mensagem_vetor(sock):
    remetente = receber_bytes(sock, 32).decode('utf-8', errors='replace').rstrip('\x00')
    N = struct.unpack('!H', receber_bytes(sock, 2))[0]
    vetor = {}
    for _ in range(N):
        dest = receber_bytes(sock, 32).decode('utf-8', errors='replace').rstrip('\x00')
        dist = struct.unpack('!B', receber_bytes(sock, 1))[0]
        vetor[dest] = dist
    return remetente, vetor

# Desmonta a mensagem de texto ('M')
def desmontar_mensagem_texto(sock):
    destino = receber_bytes(sock, 32).decode('utf-8', errors='replace').rstrip('\x00')
    texto = receber_bytes(sock, 64).decode('utf-8', errors='replace').rstrip('\x00')
    return destino, texto

# Envia mensagem de texto ('M')
def enviar_mensagem_texto(sock, destino, texto):
    msg = b'M'
    msg += struct.pack('!32s64s', destino.encode('utf-8'), texto.encode('utf-8'))
    sock.sendall(msg)

# Anuncia o vetor para todos os vizinhos
def anunciar_vetor_para_todos():
    with tabela_lock:
        vizinhos_copia = list(vizinhos.items())
    for name, sock in vizinhos_copia:
        try:
            msg = montar_mensagem_vetor(name)
            sock.sendall(msg)
        except Exception:
            pass

# Anuncia o vetor para um único vizinho
def anunciar_vetor_para_um(name):
    with tabela_lock:
        sock = vizinhos.get(name)
    if sock:
        try:
            msg = montar_mensagem_vetor(name)
            sock.sendall(msg)
        except Exception:
            pass

# Processa e limpa a conexão com um vizinho fechado
def fechar_vizinho(nome_viz):
    alterou = False
    with tabela_lock:
        if nome_viz in vizinhos:
            try:
                vizinhos[nome_viz].close()
            except Exception:
                pass
            del vizinhos[nome_viz]
            
        for dest in list(tabela.keys()):
            if tabela[dest]['nexthop'] == nome_viz:
                if tabela[dest]['dist'] != 16:
                    tabela[dest]['dist'] = 16
                    alterou = True
    if alterou:
        anunciar_vetor_para_todos()

# Processamento do Bellman-Ford
def processar_vetor(remetente, vetor_recebido):
    alterou = False
    with tabela_lock:
        for destino, dist_viz in vetor_recebido.items():
            if destino == nome_proprio:
                continue
            nova_dist = dist_viz + 1
            if nova_dist > 16:
                nova_dist = 16
                
            entrada_atual = tabela.get(destino)
            if entrada_atual is None:
                if nova_dist < 16:
                    tabela[destino] = {'nexthop': remetente, 'dist': nova_dist}
                    alterou = True
            else:
                # Atualiza se vier do mesmo nexthop OU se o custo novo for menor
                if entrada_atual['nexthop'] == remetente or nova_dist < entrada_atual['dist']:
                    if entrada_atual['dist'] != nova_dist:
                        tabela[destino]['nexthop'] = remetente
                        tabela[destino]['dist'] = nova_dist
                        alterou = True
    return alterou

# Disparo periódico do vetor
def agendar_proximo():
    with timer_lock:
        if not timer_ativo:
            return
    t = threading.Timer(INTERVALO, disparar_anuncio)
    t.daemon = True
    t.start()

def disparar_anuncio():
    anunciar_vetor_para_todos()
    agendar_proximo()

def cmd_inicio():
    global timer_ativo
    with timer_lock:
        if timer_ativo:
            return
        timer_ativo = True
    agendar_proximo()

# Processamento de comandos vindos do socket de controle
def processar_comando_controle(comando, sock):
    global vizinhos, tabela
    
    if comando == 'C':
        msg = receber_bytes(sock, 34)
        host, porto = extrai_endereco(msg)
        host_limpo = host.rstrip('\x00')
        
        nsock = None
        try:
            nsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            nsock.connect((host_limpo, porto))
            # Handshake inicial
            nsock.sendall(struct.pack('!32s', nome_proprio.encode('utf-8')))
            viz_name = receber_bytes(nsock, 32).decode('utf-8', errors='replace').rstrip('\x00')
            
            with tabela_lock:
                vizinhos[viz_name] = nsock
                tabela[viz_name] = {'nexthop': viz_name, 'dist': 1}
                
            anunciar_vetor_para_todos()
        except Exception:
            if nsock:
                try:
                    nsock.close()
                except Exception:
                    pass
            
    elif comando == 'D':
        msg = receber_bytes(sock, 32)
        roteador = extrai_roteador(msg)
        roteador_limpo = roteador.rstrip('\x00')
        fechar_vizinho(roteador_limpo)
        
    elif comando == 'T':
        with tabela_lock:
            for dest in sorted(tabela.keys()):
                info = tabela[dest]
                print(f"T {dest} {info['dist']} {info['nexthop']}", flush=True)
                
    elif comando == 'I':
        cmd_inicio()
        
    elif comando == 'E':
        msg = receber_bytes(sock, 96)
        destino, texto = extrai_destino_texto(msg)
        destino_limpo = destino.rstrip('\x00')
        texto_limpo = texto.rstrip('\x00')
        
        if destino_limpo == nome_proprio:
            print(f"R {texto_limpo}", flush=True)
        else:
            with tabela_lock:
                entrada = tabela.get(destino_limpo)
                nexthop = entrada['nexthop'] if entrada and entrada['dist'] < 16 else None
                if nexthop and nexthop in vizinhos:
                    print(f"E {destino_limpo} {nexthop} {texto_limpo}", flush=True)
                    enviar_mensagem_texto(vizinhos[nexthop], destino_limpo, texto_limpo)

# Main
def main():
    global nome_proprio, tabela
    
    if len(sys.argv) != 2:
        print("Uso:", sys.argv[0], "porto")
        sys.exit(1)
        
    server_port = int(sys.argv[1])
    
    print("I am here", end='', flush=True)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('', server_port))
    server_socket.listen()
    
    print(" at port", server_port, end='', flush=True)
    control, ctrl_addr = server_socket.accept()
    
    print(" my name is ", end='', flush=True)
    my_name_msg = receber_bytes(control, 32)
    l = struct.unpack("!32s", my_name_msg)
    my_name = l[0].decode('utf-8', errors='replace')
    print(my_name, flush=True)
    
    nome_proprio = my_name.rstrip('\x00')
    tabela[nome_proprio] = {'nexthop': nome_proprio, 'dist': 0}
    
    while True:
        with tabela_lock:
            sockets_leitura = [server_socket, control] + list(vizinhos.values())
            
        legiveis, _, _ = select.select(sockets_leitura, [], [])
        
        for s in legiveis:
            if s == server_socket:
                client_sock = None
                try:
                    client_sock, addr = server_socket.accept()
                    sender_name = receber_bytes(client_sock, 32).decode('utf-8', errors='replace').rstrip('\x00')
                    client_sock.sendall(struct.pack('!32s', nome_proprio.encode('utf-8')))
                    
                    with tabela_lock:
                        vizinhos[sender_name] = client_sock
                        tabela[sender_name] = {'nexthop': sender_name, 'dist': 1}
                        
                    anunciar_vetor_para_um(sender_name)
                    anunciar_vetor_para_todos()
                except Exception:
                    if client_sock:
                        try:
                            client_sock.close()
                        except Exception:
                            pass
                    
            elif s == control:
                try:
                    msg = s.recv(1)
                    if not msg:
                        print("Connection closed", flush=True)
                        sys.exit()
                    comando = msg.decode('utf-8')
                    processar_comando_controle(comando, s)
                except Exception:
                    print("Connection closed", flush=True)
                    sys.exit()
                    
            else:
                nome_viz = None
                with tabela_lock:
                    for nome, sock in vizinhos.items():
                        if sock == s:
                            nome_viz = nome
                            break
                            
                if nome_viz is None:
                    continue
                    
                try:
                    msg_type_bytes = s.recv(1)
                    if not msg_type_bytes:
                        fechar_vizinho(nome_viz)
                        continue
                        
                    msg_type = msg_type_bytes.decode('utf-8')
                    if msg_type == 'V':
                        remetente, vetor = desmontar_mensagem_vetor(s)
                        alterou = processar_vetor(remetente, vetor)
                        if alterou:
                            anunciar_vetor_para_todos()
                            
                    elif msg_type == 'M':
                        destino, texto = desmontar_mensagem_texto(s)
                        if destino == nome_proprio:
                            print(f"R {texto}", flush=True)
                        else:
                            with tabela_lock:
                                entrada = tabela.get(destino)
                                nexthop = entrada['nexthop'] if entrada and entrada['dist'] < 16 else None
                                if nexthop and nexthop in vizinhos:
                                    print(f"E {destino} {nexthop} {texto}", flush=True)
                                    enviar_mensagem_texto(vizinhos[nexthop], destino, texto)
                except Exception:
                    fechar_vizinho(nome_viz)

if __name__ == '__main__':
    main()
