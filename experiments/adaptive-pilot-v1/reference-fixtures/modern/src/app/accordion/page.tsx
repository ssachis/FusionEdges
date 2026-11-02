"use client";
import {Accordion} from '@/components/retroui/Accordion';
import '../accordion.css';
export default function Page(){return <main style={{maxWidth:640,margin:'32px auto',padding:16}}><h1>Native donor accordion fixture</h1><section data-fe-transfer="pilot-accordion"><Accordion type="single" collapsible><Accordion.Item value="pilot"><Accordion.Header>What does FusionEdges transfer?</Accordion.Header><Accordion.Content>Reusable UI components.</Accordion.Content></Accordion.Item></Accordion></section></main>}
