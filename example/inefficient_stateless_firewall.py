# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ryu.base import app_manager
from ryu.controller import ofp_event, dpset
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER,set_ev_cls
from ryu.ofproto import ofproto_v1_3, ofproto_v1_3_parser
from ryu.lib.packet import packet,ethernet,ipv4,udp,tcp,icmp
from ryu.ofproto.ether import ETH_TYPE_IP, ETH_TYPE_ARP,ETH_TYPE_LLDP,ETH_TYPE_MPLS,ETH_TYPE_IPV6
from ryu.ofproto.inet import IPPROTO_ICMP, IPPROTO_TCP, IPPROTO_UDP,IPPROTO_SCTP
from parse_firewall_rules import parse_firewall
from switch_information import SwitchInfo
from packet_out import SendPacket
from construct_flow import Construct
from connection_tracking import TrackConnection
ICMP_PING = 8
ICMP_PONG = 0
TCP_SYN = 2
TCP_SYN_ACK = 18

class InefficientFirewall(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    inner_policy = {}
    sendpkt = SendPacket()
    flow = Construct()
    track = TrackConnection()

    def __init__(self, *args, **kwargs):
        super(InefficientFirewall, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        parser = parse_firewall()
        self.inner_policy = parser.parse()
        self.logger.info("dict is ready")

 
    @set_ev_cls(dpset.EventDP, dpset.DPSET_EV_DISPATCHER)
    def handler_datapath(self, ev):
        SwitchInfo(ev)
    
    
    """
        Handles incoming packets. Decode them
        and check for suitable Firewall Rules.
    """
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        try:
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
            dst = eth.dst
            src = eth.src
            ethtype = eth.ethertype
            
            out_port = self.port_learn(datapath, eth, in_port)
            action_fwd_to_out_port = [parser.OFPActionOutput(out_port)]
            action_drop = [parser.OFPActionOutput(ofproto.OFPPC_NO_FWD)]
            actions_default =  action_fwd_to_out_port
    
            if(out_port != ofproto.OFPP_FLOOD) and (ethtype == ETH_TYPE_IP):
                ipo = pkt.get_protocols(ipv4.ipv4)[0]
                
                #Check for ICMP
                if(ipo.proto == IPPROTO_ICMP):
                    flag1 = 0
                    icmpob = pkt.get_protocol(icmp.icmp)
                    #Check if this is PING or PONG
                    if ((icmpob.type==ICMP_PING) and self.inner_policy.has_key(ipo.src)):
                        for i in range(0,len(self.inner_policy.get(ipo.src))):
                            if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                xyz = self.inner_policy.get(ipo.src)[i]
                                if((xyz[1]=='ICMP') and (xyz[4] == 'PING') and (xyz[5] == 'ALLOW')):
                                    flag1 = 1
                                    actions_default = action_fwd_to_out_port
                                    break

                    elif((icmpob.type == ICMP_PONG) and (self.inner_policy.has_key(ipo.src))):
                        for i in range(0,len(self.inner_policy.get(ipo.src))): 
                            if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                xyz = self.inner_policy.get(ipo.src)[i]
                                if ((xyz[1]=='ICMP') and (xyz[4] == 'PONG') and (xyz[5] == 'ALLOW')):
                                    flag1 = 1
                                    actions_default = action_fwd_to_out_port
                                    break
                                
                    if (flag1 == 0):
                        actions_default = action_drop
                    
                  
                #Check for TCP.    
                elif (ipo.proto == IPPROTO_TCP):
                    flag2 = 0
                    tcpo = pkt.get_protocol(tcp.tcp)
                    # TCP SYN packet
                    if (tcpo.bits == TCP_SYN):
                        if self.inner_policy.has_key(ipo.src):
                            for i in range(0,len(self.inner_policy.get(ipo.src))):
                                if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                    xyz = self.inner_policy.get(ipo.src)[i]
                                    if((xyz[1]=='TCP') and (int(xyz[2]) == tcpo.src_port) and (int(xyz[3]) == tcpo.dst_port) and (xyz[4] == 'NEW')  and  (xyz[5] == 'ALLOW')):
                                        flag2 = 1
                                        actions_default = action_fwd_to_out_port
                                        break
                    
                    # TCP SYN ACK packet                   
                    elif(tcpo.bits == TCP_SYN_ACK):
                        if self.inner_policy.has_key(ipo.src):
                            for i in range(0,len(self.inner_policy.get(ipo.src))):
                                if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                    xyz = self.inner_policy.get(ipo.src)[i]
                                    if((xyz[1]=='TCP') and (int(xyz[2]) == tcpo.src_port) and (int(xyz[3]) == tcpo.dst_port) and (xyz[4] == 'EST')  and  (xyz[5] == 'ALLOW')):
                                        flag2 = 1
                                        actions_default = action_fwd_to_out_port
                                        break
                                    
                    # All remaining TCP packets, like, ACK, PUSH, FIN etc.            
                    else:
                        if self.inner_policy.has_key(ipo.src):
                            for i in range(0,len(self.inner_policy.get(ipo.src))):
                                if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                    xyz = self.inner_policy.get(ipo.src)[i]
                                    if((xyz[1]=='TCP') and (int(xyz[2]) == tcpo.src_port) and (int(xyz[3]) == tcpo.dst_port) and  (xyz[5] == 'ALLOW')):
                                        flag2 = 1
                                        actions_default = action_fwd_to_out_port
                                        break
                                    
                    if (flag2 == 0):
                        actions_default = action_drop
                                
                #Check for UDP
                elif (ipo.proto == IPPROTO_UDP):
                    flag3 = 0
                    udpo = pkt.get_protocol(udp.udp)
                    if self.inner_policy.has_key(ipo.src):
                        for i in range(0,len(self.inner_policy.get(ipo.src))):
                            if self.inner_policy.get(ipo.src)[i][0] == ipo.dst:
                                xyz = self.inner_policy.get(ipo.src)[i]
                                if((xyz[1]=='UDP') and (int(xyz[2]) == udpo.src_port) and (int(xyz[3]) == udpo.dst_port) and (xyz[5] == 'ALLOW')):
                                    flag3 = 1
                                    actions_default = action_fwd_to_out_port
                                    break
                                
                    if (flag3 == 0):
                        actions_default = action_drop
                           
                else:
                    self.logger.info("Wrong IP protocol found")
                    actions_default = action_drop
            
            # Handling ARP Rules.
            elif(out_port != ofproto.OFPP_FLOOD)  and  (ethtype == ETH_TYPE_ARP):
                actions_default = action_fwd_to_out_port
                self.flow.add_flow(datapath=datapath, actions=actions_default, priority=1000, in_port = in_port, 
                              eth_type= ETH_TYPE_ARP, eth_src= src, eth_dst = dst)
                
            else:
                actions_default = action_drop
                
        except Exception as err:
            self.logger.info("MYERROR: %s" , err.message)
            action_drop = [parser.OFPActionOutput(ofproto.OFPPC_NO_FWD)]
            actions_default = action_drop
            
        finally:    
            self.sendpkt.send(datapath, msg, in_port, actions_default)
            
            
    def port_learn(self, datapath, eth_obj, in_port):
        try:
            self.mac_to_port.setdefault(datapath.id, {'90:e2:ba:1c:55:54':1 , '90:e2:ba:1c:55:55':2})
            self.mac_to_port[datapath.id][eth_obj.src] = in_port
            
            if (eth_obj.ethertype == ETH_TYPE_IP)  or  (eth_obj.ethertype == ETH_TYPE_ARP):
                    if eth_obj.dst in self.mac_to_port[datapath.id]:
                        out_port = self.mac_to_port[datapath.id][eth_obj.dst]
                    else:
                        out_port = datapath.ofproto.OFPP_FLOOD
        except Exception as err:
            self.info(err.message)
            out_port = datapath.ofproto.OFPP_FLOOD
        finally:
            return out_port