# Packet losses exporter  
  
Сервис раскатан в докер
Суть: обрабатывает конфиги из /etc/network/interfaces.d/gre*conf, с помощью регескпов вычисляет нужные поля и замеряет потерю пакетов внутри и снаружи тунеля, также выбрасывает метрику в которой есть конфиги которые не прошли по заданным регекспам(сломаные)  
